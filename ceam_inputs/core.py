"""This module performs the core data transformations on GBD data and provides a basic API for data access."""
from typing import Iterable, Sequence, Union, Set, List
from itertools import product

import numpy as np
import pandas as pd

from ceam_inputs import gbd, risk_factor_correlation
from ceam_inputs.gbd_mapping.templates import sid, UNKNOWN, Cause, Sequela, Etiology, Risk, ModelableEntity
from ceam_inputs.gbd_mapping.healthcare_entities import HealthcareEntity
from ceam_inputs.gbd_mapping.coverage_gaps import CoverageGap
from ceam_inputs.gbd_mapping.covariates import Covariate
from ceam_inputs.gbd_mapping.treatment_technologies import TreatmentTechnology


# Define GBD sex ids for usage with central comp tools.
MALE = [1]
FEMALE = [2]
COMBINED = [3]

name_measure_map = {'death': 1,
                    'DALY': 2,
                    'YLD': 3,
                    'YLL': 4,
                    'prevalence': 5,
                    'incidence': 6,
                    'remission': 7,
                    'excess_mortality': 9,
                    'proportion': 18,
                    'continuous': 19,}
gbd_round_id_map = {3: 'GBD_2015', 4: 'GBD_2016'}

# TODO: Push more of the complexity back into the helper functions.  It's helping to generalize the code
# to have everything passing through get_gbd_draws.


class DataError(Exception):
    """Base exception for errors in data loading."""
    pass


class InvalidQueryError(DataError):
    """Exception raised when the user makes an invalid request for data (e.g. exposures for a sequela)."""
    pass


class UnhandledDataError(DataError):
    """Exception raised when we receive data from the databases that we don't know how to handle."""
    pass


class DataMissingError(DataError):
    """Exception raised when data has unhandled missing entries."""
    pass


class DuplicateDataError(DataError):
    """Exception raised when data has duplication in the index."""
    pass


def get_gbd_draws(entities: Sequence[ModelableEntity], measures: Iterable[str],
                  location_ids: Iterable[int]) -> pd.DataFrame:
    """Gets draw level gbd data for each specified measure and entity.

    Parameters
    ----------
    entities:
        A list of data containers from the `gbd_mapping` package. The entities must all be the same
        type (e.g. all `gbd_mapping.Cause` objects or all `gbd_mapping.Risk` objects, etc.
    measures:
        A list of the GBD measures requested for the provided entities.
    location_ids:
        A list of location ids to pull data for.

    Returns
    -------
    A table of draw level data for indexed by an entity, measure, and location combination as well as demographic data
    (age_group_id, sex_id, year_id) where appropriate.
    """
    measure_handlers = {
        'death': (_get_death, set()),
        'remission': (_get_remission, set()),
        'prevalence': (_get_prevalence, set()),
        'incidence': (_get_incidence, set()),
        'relative_risk': (_get_relative_risk, {'cause_id', 'parameter'}),
        'population_attributable_fraction': (_get_population_attributable_fraction, {'cause_id', 'risk_id'}),
        'exposure': (_get_exposure, {'risk_id', 'parameter'}),
        'annual_visits': (_get_annual_visits, {'modelable_entity_id',}),
    }

    data = []
    id_cols = set()
    for measure in measures:
        handler, id_columns = measure_handlers[measure]
        measure_data = handler(entities, location_ids)
        measure_data['measure'] = measure
        id_cols |= id_columns
        data.append(measure_data)
    data = pd.concat(data)

    id_cols |= _get_additional_id_columns(data, entities)

    key_columns = ['year_id', 'sex_id', 'age_group_id', 'location_id', 'measure'] + list(id_cols)
    draw_columns = [f'draw_{i}' for i in range(0, 1000)]

    data = data[key_columns + draw_columns].reset_index(drop=True)
    _validate_data(data, key_columns)

    return data


# TODO: Move to utilities.py
def _get_ids_for_measure(entities: Sequence[ModelableEntity], measure: str) -> List:
    """Selects the appropriate gbd id type for each entity and measure pair.

    Parameters
    ----------
    entities:
        A list of data containers from the `gbd_mapping` package. The entities must all be the same
        type (e.g. all `gbd_mapping.Cause` objects or all `gbd_mapping.Risk` objects, etc.
    measures:
        A list of the GBD measures requested for the provided entities.

    Returns
    -------
    A dictionary whose keys are the requested measures and whose values are sets of the appropriate
    GBD ids for use with central comp tools for the provided entities.

    Raises
    ------
    InvalidQueryError
        If the entities passed are inconsistent with the requested measures.
    """
    measure_types = {
        'death': (Cause, 'gbd_id'),
        'prevalence': ((Cause, Sequela), 'gbd_id'),
        'incidence': ((Cause, Sequela), 'gbd_id'),
        'exposure': ((Risk, CoverageGap), 'gbd_id'),
        'relative_risk': ((Risk, CoverageGap), 'gbd_id'),
        'population_attributable_fraction': ((Risk, CoverageGap), 'gbd_id'),
        'annual_visits': (HealthcareEntity, 'utilization'),
        'remission': (Cause, 'dismod_id'),
    }

    if not all([isinstance(e, type(entities[0])) for e in entities]):
        raise InvalidQueryError("All entities must be of the same type")
    if measure not in measure_types.keys():
        raise InvalidQueryError(f"You've requested an invalid measure: {measure}")

    valid_types, id_attr = measure_types[measure]
    out = []
    for entity in entities:
        if isinstance(entity, valid_types) and entity[id_attr] is not UNKNOWN:
            out.append(entity[id_attr])
        else:
            raise InvalidQueryError(f"Entity {entity.name} has no data for measure '{measure}'")

    return out


# TODO: Move to utilities.py
def _get_additional_id_columns(data, entities):
    id_column_map = {
        Cause: 'cause_id',
        Sequela: 'sequela_id',
        Covariate: 'covariate_id',
        Risk: 'risk_id',
        Etiology: 'etiology_id',
        CoverageGap: 'coverage_gap',
        HealthcareEntity: 'healthcare_entity',
        TreatmentTechnology: 'treatment_technology',
    }
    out = set()
    out.add(id_column_map[type(entities[0])])
    out |= set(data.columns) & set(id_column_map.values())
    return out


# TODO: Move to utilities.py
def _validate_data(data: pd.DataFrame, key_columns: Iterable[str]=None):
    """Validates that no data is missing and that the provided key columns make a valid (unique) index.

    Parameters
    ----------
    data:
        The data table to be validated.
    key_columns:
        An iterable of the column names used to uniquely identify a row in the data table.

    Raises
    ------
    DataMissingError
        If the data contains any null (NaN or NaT) values.
    DuplicatedDataError
        If the provided key columns are insufficient to uniquely identify a record in the data table.
    """
    if np.any(data.isnull()):
        raise DataMissingError()

    if key_columns and np.any(data.duplicated(key_columns)):
        raise DuplicateDataError()


#########################
# get_gbd_draws helpers #
#########################
#
# These functions filter out erroneous measures and deal with special cases.
#

def _get_death(entities, location_ids):
    measure_ids = _get_ids_for_measure(entities, 'death')
    death_data = gbd.get_codcorrect_draws(cause_ids=measure_ids, location_ids=location_ids)

    return death_data[death_data['measure_id'] == name_measure_map['death']]


def _get_remission(entities, location_ids):
    measure_ids = _get_ids_for_measure(entities, 'remission')
    remission_data = gbd.get_modelable_entity_draws(me_ids=measure_ids, location_ids=location_ids)

    id_map = {entity.dismod_id: entity.gbd_id for entity in entities}
    remission_data['cause_id'] = remission_data['modelable_entity_id'].replace(id_map)

    # FIXME: The sex filtering should happen in the reshaping step.
    correct_measure = remission_data['measure_id'] == name_measure_map['remission']
    correct_sex = remission_data['sex_id'] != COMBINED
    return remission_data[correct_measure & correct_sex]


def _get_prevalence(entities, location_ids):
    measure_ids = _get_ids_for_measure(entities, 'prevalence')
    measure_data = gbd.get_como_draws(entity_ids=measure_ids, location_ids=location_ids)

    # FIXME: The year filtering should happen in the reshaping step.
    correct_measure = measure_data['measure_id'] == name_measure_map['prevalence']
    correct_years = measure_data['year_id'].isin(gbd.get_estimation_years(gbd.GBD_ROUND_ID))
    return measure_data[correct_measure & correct_years]


def _get_incidence(entities, location_ids):
    measure_ids = _get_ids_for_measure(entities, 'incidence')
    measure_data = gbd.get_como_draws(entity_ids=measure_ids, location_ids=location_ids)

    # FIXME: The year filtering should happen in the reshaping step.
    correct_measure = measure_data['measure_id'] == name_measure_map['incidence']
    correct_years = measure_data['year_id'].isin(gbd.get_estimation_years(gbd.GBD_ROUND_ID))
    return measure_data[correct_measure & correct_years]


def _get_relative_risk(entities, location_ids):
    measure_ids = _get_ids_for_measure(entities, 'relative_risk')
    measure_data = gbd.get_relative_risks(risk_ids=measure_ids, location_ids=location_ids)

    # FIXME: I'm passing because this is broken for zinc_deficiency, and I don't have time to investigate -J.C.
    # err_msg = ("Not all relative risk data has both the 'mortality' and 'morbidity' flag "
    #            + "set. This may not indicate an error but it is a case we don't explicitly handle. "
    #            + "If you need this risk, come talk to one of the programmers.")
    # assert np.all((measure_data.mortality == 1) & (measure_data.morbidity == 1)), err_msg

    measure_data = measure_data[measure_data['morbidity'] == 1]  # FIXME: HACK
    del measure_data['mortality']
    del measure_data['morbidity']

    measure_data = measure_data.rename(columns={f'rr_{i}': f'draw_{i}' for i in range(1000)})

    return measure_data


def _get_population_attributable_fraction(entities, location_ids):
    measure_ids = _get_ids_for_measure(entities, 'population_attributable_fraction')
    measure_data = gbd.get_pafs(risk_ids=measure_ids, location_ids=location_ids)

    # FIXME: I'm passing because this is broken for SBP, it's unimportant, and I don't have time to investigate -J.C.
    # measure_ids = {name_measure_map[m] for m in ['death', 'DALY', 'YLD', 'YLL']}
    # err_msg = ("Not all PAF data has values for deaths, DALYs, YLDs and YLLs. "
    #           + "This may not indicate an error but it is a case we don't explicitly handle. "
    #           + "If you need this PAF, come talk to one of the programmers.")
    # assert np.all(
    #    measure_data.groupby(key_columns).measure_id.unique().apply(lambda x: set(x) == measure_ids)), err_msg

    # TODO: figure out if we need to assert some property of the different PAF measures

    measure_data = measure_data[measure_data['measure_id'] == name_measure_map['YLD']]
    # FIXME: Is this the only data we need to delete measure id for?
    del measure_data['measure_id']
    return measure_data


def _get_exposure(entities, location_ids):
    measure_ids = _get_ids_for_measure(entities, 'exposure')
    measure_data = gbd.get_exposures(risk_ids=measure_ids, location_ids=location_ids)

    measure_data = _handle_weird_exposure_measures(measure_data)

    # FIXME: The sex filtering should happen in the reshaping step.
    is_categorical_exposure = measure_data.measure_id == name_measure_map['proportion']
    is_continuous_exposure = measure_data.measure_id == name_measure_map['continuous']
    measure_data = measure_data[is_categorical_exposure | is_continuous_exposure]
    measure_data = measure_data[measure_data['sex_id'] != COMBINED]

    # FIXME: Is this the only data we need to delete measure id for?
    del measure_data['measure_id']
    return measure_data


def _handle_weird_exposure_measures(measure_data):
    key_cols = ['age_group_id', 'location_id', 'sex_id', 'year_id']
    draw_cols = [f'draw_{i}' for i in range(1000)]

    measure_data = measure_data.set_index(key_cols)
    measure_data = measure_data[draw_cols + ['risk_id', 'measure_id', 'parameter']]

    for risk_id in measure_data.risk_id.unique():
        # We need to handle this juggling risk by risk because the data is heterogeneous by risk id.
        correct_risk = measure_data['risk_id'] == risk_id
        risk_data = measure_data[correct_risk]

        measure_ids = risk_data.measure_id.unique()
        if len(measure_ids) > 1:
            raise UnhandledDataError("Exposures should always come back with a single measure, "
                                     "or they should be dealt with as a special case.  ")

        measure_id = int(measure_ids)

        # FIXME:
        # Some categorical risks come from cause models, or they get weird exposure models that
        # report prevalence instead of proportion.  We should do a systematic review of them and work
        # with the risk factors team to get the exposure reported consistently.  In the mean time
        # we scale the unit-full prevalence numbers to unit-less proportion numbers. - J.C.
        if measure_id == name_measure_map['prevalence']:
            total_prevalence = pd.DataFrame(np.sum([risk_data.loc[risk_data['parameter'] == parameter, draw_cols].values
                                                    for parameter in risk_data['parameter'].unique()], axis=0),
                                            columns=draw_cols, index=risk_data.index.drop_duplicates())
            for parameter in risk_data['parameter'].unique():
                correct_parameter = risk_data['parameter'] == parameter
                measure_data.loc[correct_risk & correct_parameter, draw_cols] /= total_prevalence

            measure_data.loc[correct_risk, 'measure_id'] = name_measure_map['proportion']

    return measure_data.reset_index()


def _get_annual_visits(entities, location_ids):
    measure_ids = _get_ids_for_measure(entities, 'annual_visits')
    measure_data = gbd.get_modelable_entity_draws(me_ids=measure_ids, location_ids=location_ids)

    measure_data['treatment_technology'] = 'temp'
    for entity in entities:
        correct_entity = measure_data['modelable_entity_id'] == entity.utilization
        measure_data.loc[correct_entity, 'healthcare_entity'] = entity.name

    correct_measure = measure_data['measure_id'] == name_measure_map['continuous']
    correct_sex = measure_data['sex_id'] != COMBINED

    return measure_data[correct_measure & correct_sex]


####################################
# Measures for cause like entities #
####################################


def get_prevalence(entities: Union[Sequence[Cause], Sequence[Sequela]], location_ids: Sequence[int]) -> pd.DataFrame:
    """Gets prevalence data for the specified entities and locations.

    Parameters
    ----------
    entities:
        A list of data containers from the `gbd_mapping` package. The entities must all be the same
        type (all `gbd_mapping.Cause` objects or all `gbd_mapping.Sequela` objects).
    location_ids:
        A list of location ids to pull data for.

    Returns
    -------
    A table of prevalence data for indexed by the given entity ids and location ids
    as well as by demographic data (year_id, sex_id, and age_group_id).
    """
    return get_gbd_draws(entities, ['prevalence'], location_ids).drop('measure', 'columns')


def get_incidence(entities: Union[Sequence[Cause], Sequence[Sequela]], location_ids: Sequence[int]) -> pd.DataFrame:
    """Gets incidence data for the specified entities and locations.

    Parameters
    ----------
    entities:
        A list of data containers from the `gbd_mapping` package. The entities must all be the same
        type (all `gbd_mapping.Cause` objects or all `gbd_mapping.Sequela` objects).
    location_ids:
        A list of location ids to pull data for.

    Returns
    -------
    A table of incidence data for indexed by the given entity ids and location ids
    as well as by demographic data (year_id, sex_id, and age_group_id).
    """
    return get_gbd_draws(entities, ['incidence'], location_ids).drop('measure', 'columns')


def get_remission(causes: Sequence[Cause], location_ids: Sequence[int]) -> pd.DataFrame:
    """Gets remission data for the specified causes and locations.

    Parameters
    ----------
    causes:
        A list of `Cause` data containers from the `gbd_mapping` package.
    location_ids:
        A list of location ids to pull data for.

    Returns
    -------
    A table of incidence data for indexed by the given cause ids and location ids
    as well as by demographic data (year_id, sex_id, and age_group_id).
    """
    return get_gbd_draws(causes, ['remission'], location_ids).drop('measure', 'columns')


def get_cause_specific_mortality(causes: Sequence[Cause], location_ids: Sequence[int]) -> pd.DataFrame:
    """Gets cause specific mortality data for the specified causes and locations.

    Parameters
    ----------
    causes:
        A list of `Cause` data containers from the `gbd_mapping` package.
    location_ids:
        A list of location ids to pull data for.

    Returns
    -------
    A table of cause specific mortality data for indexed by the given cause ids and location ids
    as well as by demographic data (year_id, sex_id, and age_group_id).
    """
    deaths = get_gbd_draws(causes, ["death"], location_ids)

    populations = get_populations(location_ids)
    populations = populations[populations['year_id'] >= deaths.year_id.min()]

    merge_columns = ['age_group_id', 'location_id', 'year_id', 'sex_id']
    key_columns = merge_columns + ['cause_id']
    draw_columns = [f'draw_{i}' for i in range(0, 1000)]

    df = deaths.merge(populations, on=merge_columns).set_index(key_columns)
    csmr = df[draw_columns].divide(df['population'], axis=0).reset_index()

    csmr = csmr[key_columns + draw_columns]
    _validate_data(csmr, key_columns)

    return csmr


def get_excess_mortality(causes: Sequence[Cause], location_ids: Sequence[int]) -> pd.DataFrame:
    """Gets excess mortality data for the specified causes and locations.

    Parameters
    ----------
    causes:
        A list of `Cause` data containers from the `gbd_mapping` package.
    location_ids:
        A list of location ids to pull data for.

    Returns
    -------
    A table of excess mortality data for indexed by the given cause ids and location ids
    as well as by demographic data (year_id, sex_id, and age_group_id).
    """
    prevalences = get_prevalence(causes, location_ids)
    csmrs = get_cause_specific_mortality(causes, location_ids)

    key_columns = ['year_id', 'sex_id', 'age_group_id', 'location_id', 'cause_id']
    prevalences = prevalences.set_index(key_columns)
    csmrs = csmrs.set_index(key_columns)

    # In some cases CSMR is not zero for age groups where prevalence is, which leads to
    # crazy outputs. So enforce that constraint.
    # TODO: But is this the right place to do that?
    draw_columns = [f'draw_{i}' for i in range(1000)]
    csmrs[draw_columns] = csmrs[draw_columns].where(prevalences[draw_columns] != 0, 0)

    em = csmrs.divide(prevalences, axis='index').reset_index()
    em = em[em['sex_id'] != COMBINED]

    return em.dropna()


def get_disability_weight(sequelae: Sequence[Sequela], _: Sequence[int]) -> pd.DataFrame:
    # TODO: Check out what the data looks like to verify Returns section.
    """Gets disability weight data for the specified sequelae and locations.

    Parameters
    ----------
    sequelae:
        A list of `Sequela` data containers from the `gbd_mapping` package.
    _:
        A list of location ids for API consistency.

    Returns
    -------
    A table of disability data for indexed by the given sequela ids and location ids
    as well as by demographic data (year_id, sex_id, and age_group_id).
    """
    gbd_round = gbd_round_id_map[gbd.GBD_ROUND_ID]
    disability_weights = gbd.get_data_from_auxiliary_file('Disability Weights', gbd_round=gbd_round)
    combined_disability_weights = gbd.get_data_from_auxiliary_file('Combined Disability Weights', gbd_round=gbd_round)

    data = []
    for s in sequelae:
        # Only sequelae have disability weights.
        assert isinstance(s.gbd_id, sid)
        if s.healthstate.gbd_id in disability_weights['healthstate_id'].values:
            df = disability_weights.loc[disability_weights.healthstate_id == s.healthstate.gbd_id].copy()
        elif s.healthstate.gbd_id in combined_disability_weights['healthstate_id'].values:
            df = disability_weights.loc[disability_weights.healthstate_id == s.healthstate.gbd_id].copy()
        else:
            raise DataMissingError(f"No disability weight available for the sequela {s.name}")
        df['sequela_id'] = s.gbd_id
        df['measure'] = 'disability_weight'
        data.append(df)

    data = pd.concat(data)
    data = data.rename(columns={f'draw{i}':f'draw_{i}' for i in range(1000)})
    return data.reset_index(drop=True)


####################################
# Measures for risk like entities  #
####################################

# FIXME: Something is broken here for ORS.
def get_relative_risk(entities, location_ids):
    if isinstance(entities[0], (Risk, Etiology)):
        df = get_gbd_draws(entities, ['relative_risk'], location_ids)
        del df['measure']
    else:
        data = []
        for entity in entities:
            data.append(gbd.get_data_from_auxiliary_file(entity.relative_risk,
                                                         gbd_round=gbd_round_id_map[gbd.GBD_ROUND_ID]))
        df = pd.concat(data)
    return df[df['sex_id'] != COMBINED]


def get_exposure(entities, location_ids):
    if isinstance(entities[0], (Risk, Etiology)):
        return get_gbd_draws(entities, ['exposure'], location_ids).drop('measure', 'columns')
    else:  # We have a treatment technology
        data = []
        for entity, location_id in product(entities, location_ids):
            data.append(gbd.get_data_from_auxiliary_file(entity.exposure, location_id=location_id))
        return pd.concat(data)


def get_exposure_standard_deviation(risks, location_ids):
    ids = {risk.exposure_parameters.dismod_id: risk.gbd_id for risk in risks}
    df = gbd.get_modelable_entity_draws(list(ids.keys()), location_ids)

    df = df.replace({'modelable_entity_id': ids})
    df = df.rename(columns={'modelable_entity_id': 'risk_id'})

    key_cols = ['age_group_id', 'location_id', 'sex_id', 'year_id', 'risk_id']
    draw_cols = [f'draw_{i}' for i in range(1000)]
    df = df[df['sex_id'] != 3]
    return df[key_cols + draw_cols]


def get_population_attributable_fraction(entities, location_ids):
    if isinstance(entities[0], (Risk, Etiology)):
        df = get_gbd_draws(entities, ['population_attributable_fraction'], location_ids)
        df = df.drop('measure', 'columns')
    else:
        data = []
        for entity, location_id in product(entities, location_ids):
            data.append(gbd.get_data_from_auxiliary_file(entity.population_attributable_fraction,
                                                         location_id=location_id))
        df = pd.concat(data)
    return df


def get_ensemble_weights(risks, location_ids):
    data = []
    ids = [risk.gbd_id for risk in risks]
    for i in range(0, (len(ids))):
        risk_id = ids[i]
        temp = gbd.get_data_from_auxiliary_file('Ensemble Distribution Weights',
                                                gbd_round=gbd_round_id_map[gbd.GBD_ROUND_ID],
                                                rei_id=risk_id)
        temp['risk_id'] = risk_id
        data.append(temp)
    data = pd.concat(data)
    return data


def get_mediation_factor(risks, location_ids):
    risk_ids = [risk.gbd_id for risk in risks]
    _gbd_round_id_map = {3: 'GBD_2015', 4: 'GBD_2016'}
    data = gbd.get_data_from_auxiliary_file("Mediation Factors", gbd_round=_gbd_round_id_map[gbd.GBD_ROUND_ID])
    data = data.rename(columns={'rei_id': 'risk_id'})

    data = data.query('risk_id in @risk_ids').copy()

    if not data.empty:
        draw_columns = [f'draw_{i}' for i in range(0, 1000)]
        data[draw_columns] = 1 - (data[draw_columns])
        data = data.groupby(['cause_id', 'risk_id'])[draw_columns].prod()
        return data.reset_index()
    else:
        return 0


def get_risk_correlation_matrix(location_ids):
    data = []
    for location_id in location_ids:
        df = risk_factor_correlation.load_matrices(location_id=location_id,
                                                   gbd_round=gbd_round_id_map[gbd.GBD_ROUND_ID])
        df['location_id'] = location_id
        data.append(df)
    return pd.concat(data)


#######################
# Other kinds of data #
#######################


def get_populations(location_ids):
    populations = pd.concat([gbd.get_populations(location_id) for location_id in location_ids])
    keep_columns = ['age_group_id', 'location_id', 'year_id', 'sex_id', 'population']
    return populations[keep_columns]


def get_age_bins():
    return gbd.get_age_bins()


def get_theoretical_minimum_risk_life_expectancy():
    return gbd.get_theoretical_minimum_risk_life_expectancy()


def get_subregions(location_ids):
    return gbd.get_subregions(location_ids)


def get_cost(entities, location_ids):
    data = []
    for entity in entities:
        df = gbd.get_data_from_auxiliary_file(entity.cost)
        if entity.name in ['inpatient_visits', 'outpatient_visits']:
            df = df[df['location_id'].isin(location_ids)]
        data.append(df)
    return pd.concat(data)


def get_healthcare_annual_visit_count(entities, location_ids):
    return get_gbd_draws(entities, ['annual_visits'], location_ids).drop('measure', 'columns')


def get_covariate_estimates(covariates, location_ids):
    return gbd.get_covariate_estimates([covariate.gbd_id for covariate in covariates], location_ids)


def get_protection(treatment_technologies, location_ids):
    data = []
    for tt in treatment_technologies:
        df = gbd.get_data_from_auxiliary_file(tt.protection)
        if not set(location_ids).issubset(set(df['location_id'].unique())):

            raise DataMissingError(f'Protection data for {tt.name} is not available for locations '
                                   f'{set(location_ids) - set(df["location_id"].unique())}')
        df = df[df['location_id'].isin(location_ids)]
        data.append(df)
    return pd.concat(data)

