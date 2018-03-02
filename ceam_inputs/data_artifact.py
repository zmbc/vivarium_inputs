import os
from collections import defaultdict
import multiprocessing
from random import shuffle

import pandas as pd

from ceam_inputs import core
from ceam_inputs.utilities import normalize_for_simulation, get_age_group_midpoint_from_age_group_id

from .gbd import get_estimation_years, get_covariate_estimates, GBD_ROUND_ID
from .gbd_mapping import (causes, risk_factors, sequelae, healthcare_entities,
                          treatment_technologies, coverage_gaps, etiologies, covariates)

import logging
_log = logging.getLogger(__name__)


class DataArtifactError(Exception):
    pass

class EntityError(DataArtifactError):
    pass


def split_entity_path(path: str):
    """ Split a entity path name of the form entity_type[.entity_name] into it's components
    """

    entity_path_components = path.split(".")
    if len(entity_path_components) == 2:
        entity_type, entity_name = entity_path_components
    elif len(entity_path_components) == 1:
        entity_type = entity_path_components[0]
        entity_name = None
    else:
        raise EntityError(f"Unparsable entity_path: {path}")

    return entity_type, entity_name


class _EntityConfig:
    """ A representation of an entity and the context in which to load it's data.
    """
    def __init__(self, entity_type, name, locations, year_start, year_end, modeled_causes, entity=None):
        self.type = entity_type
        self.name = name
        self.locations = locations
        self.year_start = year_start
        self.year_end = year_end
        self.modeled_causes = modeled_causes
        self.entity = entity


def _normalize(data: pd.DataFrame) -> pd.DataFrame:
    """ Do basic normalizations to remove GBD specific column names and concepts and
    to make the dataframe long over draws.
    """
    data = normalize_for_simulation(data)
    if "age_group_id" in data:
        data = get_age_group_midpoint_from_age_group_id(data)
    draw_columns = [c for c in data.columns if "draw_" in c]
    index_columns = [c for c in data.columns if "draw_" not in c]
    data = pd.melt(data, id_vars=index_columns, value_vars=draw_columns, var_name="draw")
    data["draw"] = data.draw.str.partition("_")[2].astype(int)
    return data


def _entities_by_type(entities):
    """ Split a list of entity paths and group the entity names by entity type.
    """
    entity_by_type = defaultdict(set)
    for entity_path in entities:
        entity_type, entity_name = split_entity_path(entity_path)
        entity_by_type[entity_type].add(entity_name)
    return entity_by_type


class ArtifactBuilder:
    """ Accumulates requests for entity data and then loads the data necessary to fulfill those requests
    into a HDF file on disk which can be loaded by a simulation process.
    """

    def __init__(self):
        self.entities = set()


    def data_container(self, entity_path: str):
        """ Records a requenst for entity data for future processing
        """
        self.entities.add(entity_path)
        _log.info(f"Adding {entity_path} to list of datasets to load")


    def process(self, path, locations, parallelism=None, loaders=None):
        """ Loads data for all requensted entities within the specified locations and saves it into an
        HDF file at the specified path. By default it will use as many processes as there are CPUs. The
        data loading process can be memory intensive. To reduce peak consumption, reduce parallelism.
        """

        if loaders is None:
            loaders = LOADERS

        locations = locations

        estimation_years = get_estimation_years(GBD_ROUND_ID)
        year_start = min(estimation_years)
        year_end = max(estimation_years)

        entity_by_type = _entities_by_type(self.entities)

        age_bins = core.get_age_bins()
        dimensions = [range(year_start, year_end+1), ["Male", "Female"], age_bins.age_group_id, locations]
        dimensions = pd.MultiIndex.from_product(dimensions, names=["year", "sex", "age_group_id", "location_id"])
        dimensions = dimensions.to_frame().reset_index(drop=True)
        _dump(dimensions, "dimensions", None, "full_space", path)

        if parallelism is None:
            parallelism = multiprocessing.cpu_count()

        lock_manager = multiprocessing.Manager()
        lock = lock_manager.Lock()

        pool = multiprocessing.Pool(parallelism)
        by_type = list(entity_by_type.items())
        shuffle(by_type)
        jobs = []
        for entity_type, entities in by_type:
            for entity_name in entities:
                entity_config = _EntityConfig(entity_type=entity_type,
                                              name=entity_name,
                                              year_start=year_start,
                                              year_end=year_end,
                                              locations=locations,
                                              modeled_causes=entity_by_type["cause"])
                if parallelism > 1:
                    jobs.append(pool.apply_async(_worker, (entity_config, path, loaders[entity_type], lock)))
                else:
                    _worker(entity_config, path, loaders[entity_type], lock)
        pool.close()
        pool.join()


def _worker(entity_config, path, loader, lock):
    _log.info(f"Loading data for {entity_config.type}.{entity_config.name}")
    def writer(measure, data):
        if isinstance(data, pd.DataFrame) and "year" in data:
            data = data.loc[(data.year >= entity_config.year_start) & (data.year <= entity_config.year_end)]

        lock.acquire()
        try:
            _dump(data, entity_config.type, entity_config.name, measure, path)
        finally:
            lock.release()
    loader(entity_config, writer)


def _dump(data, entity_type, entity_name, measure, path):
    """ Write a dataset out to the target HDF file keyed by the entity the data corrisponds to.
    """
    key_components = ["/", entity_type]
    if entity_name:
        key_components.append(entity_name)

    key = os.path.join(*(key_components + [measure]))
    with pd.HDFStore(path, complevel=9, format="table") as store:
        store.put(key, data, format="table")


def _load_cause(entity_config, writer):
    measures = ["death", "prevalence", "incidence", "cause_specific_mortality", "excess_mortality"]
    result = core.get_draws([causes[entity_config.name]], measures, entity_config.locations)
    result = _normalize(result)
    for key, group in result.groupby("measure"):
        writer(key, group)
    del result

    try:
        measures = ["remission"]
        result = core.get_draws([causes[entity_config.name]], measures, entity_config.locations)
        result["cause_id"] = causes[entity_config.name].gbd_id
        writer("remission", result)
    except core.InvalidQueryError:
        pass


def _load_risk_factor(entity_config, writer):
    if entity_config.name == "correlations":
        #TODO: weird special case but this groups it with the other risk data which  I think makes sense
        correlations = core.get_risk_correlation_matrix(entity_config.locations)
        writer("correlations", correlations)
        return

    risk = risk_factors[entity_config.name]

    rrs = core.get_draws([risk], ["relative_risk"], entity_config.locations)
    normalized = []
    for key, group in rrs.groupby(["parameter", "cause_id"]):
        group = group.drop(["cause_id", "parameter"], axis=1)
        group = _normalize(group)
        group["parameter"] = key[0]
        group["cause_id"] = key[1]
        dims = ["year", "sex", "measure", "age", "age_group_start",
                "age_group_end", "location_id", "draw", "cause_id", "parameter"]
        normalized.append(group.set_index(dims))
    writer("relative_risk", pd.concat(normalized))
    del normalized

    mfs = core.get_draws([risk], ["mediation_factor"], entity_config.locations)
    if not mfs.empty:
        # Not all risks have mediation factors
        index_columns = [c for c in mfs.columns if "draw_" not in c]
        draw_columns = [c for c in mfs.columns if "draw_" in c]
        mfs = pd.melt(mfs, id_vars=index_columns, value_vars=draw_columns, var_name="draw")
        mfs["draw"] = mfs.draw.str.partition("_")[2].astype(int)
        writer("mediation_factor", mfs)
        del mfs

    pafs = core.get_draws([risk], ["population_attributable_fraction"], entity_config.locations)
    normalized = []
    for key, group in pafs.groupby(["cause_id"]):
        group = group.drop(["cause_id"], axis=1)
        group = _normalize(group)
        group["cause_id"] = key
        dims = ["year", "sex", "measure", "age", "age_group_start", "age_group_end", "location_id", "draw", "cause_id"]
        normalized.append(group.set_index(dims))
    writer("population_attributable_fraction", pd.concat(normalized))
    del normalized

    exposures = core.get_draws([risk], ["exposure"], entity_config.locations)
    normalized = []
    for key, group in exposures.groupby(["parameter"]):
        group = group.drop(["parameter"], axis=1)
        group = _normalize(group)
        group["parameter"] = key
        dims = ["year", "sex", "measure", "age", "age_group_start", "age_group_end", "location_id", "draw", "parameter"]
        normalized.append(group.set_index(dims))
    writer("exposure", pd.concat(normalized))
    del normalized

    if risk.exposure_parameters is not None:
        exposure_stds = core.get_draws([risk], ["exposure_standard_deviation"], entity_config.locations)
        exposure_stds = _normalize(exposure_stds)
        writer("exposure_standard_deviation", exposure_stds)

def _load_sequela(entity_config, writer):
    sequela = sequelae[entity_config.name]
    measures = ["prevalence", "incidence"]
    result = core.get_draws([sequela], measures, entity_config.locations).drop("sequela_id", axis=1)
    result = _normalize(result)
    result["sequela_id"] = sequela.gbd_id
    for key, group in result.groupby("measure"):
        writer(key, group)
    del result


    weights = core.get_draws([sequela], ["disability_weight"], entity_config.locations)
    index_columns = [c for c in weights.columns if "draw_" not in c]
    draw_columns = [c for c in weights.columns if "draw_" in c]
    weights = pd.melt(weights, id_vars=index_columns, value_vars=draw_columns, var_name="draw")
    weights["draw"] = weights.draw.str.partition("_")[2].astype(int)
    writer("disability_weight", weights)

def _load_healthcare_entity(entity_config, writer):
    healthcare_entity = healthcare_entities[entity_config.name]

    cost = core.get_draws([healthcare_entity], ["cost"], entity_config.locations)
    cost = _normalize(cost)
    writer("cost", cost)

    annual_visits = core.get_draws([healthcare_entity], ["annual_visits"], entity_config.locations)
    annual_visits = _normalize(annual_visits)
    writer("annual_visits", annual_visits)


def _load_treatment_technology(entity_config, writer):
    treatment_technology = treatment_technologies[entity_config.name]

    if treatment_technology.protection:
        try:
            protection = core.get_draws([treatment_technology], ["protection"], entity_config.locations)
            protection = _normalize(protection)
            writer("protection", protection)
        except core.DataMissingError:
            pass

    if treatment_technology.relative_risk:
        relative_risk = core.get_draws([treatment_technology], ["relative_risk"], entity_config.locations)
        relative_risk = _normalize(relative_risk)
        writer("relative_risk", relative_risk)

    if treatment_technology.exposure:
        try:
            exposure = core.get_draws([treatment_technology], ["exposure"], entity_config.locations)
            exposure = _normalize(exposure)
            writer("exposure", exposure)
        except core.DataMissingError:
            pass

    if treatment_technology.cost:
        cost = core.get_draws([treatment_technology], ["cost"], entity_config.locations)
        cost = _normalize(cost)
        writer("cost", cost)

def _load_coverage_gap(entity_config, writer):
    entity = coverage_gaps[entity_config.name]

    try:
        exposure = core.get_draws([entity], ["exposure"], entity_config.locations)
        exposure = _normalize(exposure)
        writer("exposure", exposure)
    except core.InvalidQueryError:
        pass

    mediation_factor = core.get_draws([entity], ["mediation_factor"], entity_config.locations)
    if not mediation_factor.empty:
        #TODO: This should probably be an exception. It looks like James was in the middle of doing better
        # error handling in ceam_inputs.core but hasn"t finished yet
        mediation_factor = _normalize(mediation_factor)
        writer("mediation_factor", mediation_factor)

    relative_risk = core.get_draws([entity], ["relative_risk"], entity_config.locations)
    relative_risk = _normalize(relative_risk)
    writer("relative_risk", relative_risk)


    paf = core.get_draws([entity], ["population_attributable_fraction"], entity_config.locations)
    paf = _normalize(paf)
    writer("population_attributable_fraction", paf)

def _load_etiology(entity_config, writer):
    entity = etiologies[entity_config.name]

    paf = core.get_draws([entity], ["population_attributable_fraction"],
                                                      entity_config.locations)
    paf = _normalize(paf)
    writer("population_attributable_fraction", paf)


def _load_population(entity_config, writer):
    pop = core.get_populations(entity_config.locations)
    pop = normalize_for_simulation(pop)
    pop = get_age_group_midpoint_from_age_group_id(pop)
    writer("structure", pop)

    bins = core.get_age_bins()[["age_group_years_start", "age_group_years_end", "age_group_name"]]
    bins = bins.rename(columns={"age_group_years_start": "age_group_start", "age_group_years_end": "age_group_end"})
    writer("age_bins", bins)

    writer("theoretical_minimum_risk_life_expectancy", core.get_theoretical_minimum_risk_life_expectancy())


def _load_covariate(entity_config, writer):
    entity = covariates[entity_config.name]
    estimate = get_covariate_estimates([entity.gbd_id], entity_config.locations)

    if entity is covariates.age_specific_fertility_rate:
        columns = ["location_id", "mean_value", "lower_value", "upper_value", "age_group_id", "sex_id", "year_id"]
        estimate = estimate[columns]
        estimate = get_age_group_midpoint_from_age_group_id(estimate)
        estimate = normalize_for_simulation(estimate)
    elif entity in (covariates.live_births_by_sex, covariates.dtp3_coverage_proportion):
        columns = ["location_id", "mean_value", "lower_value", "upper_value", "sex_id", "year_id"]
        estimate = estimate[columns]
        estimate = normalize_for_simulation(estimate)
    writer("estimate", estimate)


def _load_subregions(entity_config, writer):
    df = pd.DataFrame(core.get_subregions(entity_config.locations))
    df = df.melt(var_name="location", value_name="subregion_id")
    writer("sub_region_ids", df)


LOADERS = {
    "cause": _load_cause,
    "risk_factor": _load_risk_factor,
    "sequela": _load_sequela,
    "population": _load_population,
    "healthcare_entity": _load_healthcare_entity,
    "treatment_technology": _load_treatment_technology,
    "coverage_gap": _load_coverage_gap,
    "etiology": _load_etiology,
    "covariate": _load_covariate,
    "subregions": _load_subregions,
}
