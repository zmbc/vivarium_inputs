from typing import Union
from itertools import product
import warnings

from gbd_mapping import Cause, Sequela, RiskFactor, CoverageGap, Etiology, Covariate, causes
import pandas as pd
import numpy as np

from vivarium_inputs import utilities, extract, utility_data
from vivarium_inputs.globals import (InvalidQueryError, DEMOGRAPHIC_COLUMNS, MEASURES, SEXES, DRAW_COLUMNS,
                                     Population, DataDoesNotExistError, EXTRA_RESIDUAL_CATEGORY)
from vivarium_inputs.mapping_extension import AlternativeRiskFactor, HealthcareEntity, HealthTechnology


DISTRIBUTION_COLUMNS = ['exp', 'gamma', 'invgamma', 'llogis', 'gumbel', 'invweibull', 'weibull',
                         'lnorm', 'norm', 'glnorm', 'betasr', 'mgamma', 'mgumbel']
COVARIATE_VALUE_COLUMNS = ['mean_value', 'upper_value', 'lower_value']


def get_data(entity, measure: str, location: Union[str, int]):
    measure_handlers = {
        # Cause-like measures
        'incidence': (get_incidence, ('cause', 'sequela')),
        'raw_incidence': (get_raw_incidence, ('cause', 'sequela')),
        'prevalence': (get_prevalence, ('cause', 'sequela')),
        'birth_prevalence': (get_birth_prevalence, ('cause', 'sequela')),
        'disability_weight': (get_disability_weight, ('cause', 'sequela')),
        'remission': (get_remission, ('cause',)),
        'cause_specific_mortality': (get_cause_specific_mortality, ('cause',)),
        'excess_mortality': (get_excess_mortality, ('cause',)),
        'case_fatality': (get_case_fatality, ('cause',)),
        'deaths': (get_deaths, ('cause',)),
        # Risk-like measures
        'exposure': (get_exposure, ('risk_factor', 'coverage_gap', 'alternative_risk_factor',)),
        'exposure_standard_deviation': (get_exposure_standard_deviation, ('risk_factor', 'alternative_risk_factor')),
        'exposure_distribution_weights': (get_exposure_distribution_weights, ('risk_factor', 'alternative_risk_factor')),
        'relative_risk': (get_relative_risk, ('risk_factor', 'coverage_gap')),
        'population_attributable_fraction': (get_population_attributable_fraction, ('risk_factor', 'etiology')),
        'mediation_factors': (get_mediation_factors, ('risk_factor',)),
        # Covariate measures
        'estimate': (get_estimate, ('covariate',)),
        # Health system measures
        'cost': (get_cost, ('healthcare_entity', 'health_technology')),
        'utilization': (get_utilization, ('healthcare_entity',)),
        # Population measures
        'structure': (get_structure, ('population',)),
        'theoretical_minimum_risk_life_expectancy': (get_theoretical_minimum_risk_life_expectancy, ('population',)),
        'age_bins': (get_age_bins, ('population',)),
        'demographic_dimensions': (get_demographic_dimensions, ('population',))

    }

    if measure not in measure_handlers:
        raise InvalidQueryError(f'No functions available to pull data for measure {measure}.')

    handler, entity_types = measure_handlers[measure]

    if entity.kind not in entity_types:
        raise InvalidQueryError(f'{measure.capitalize()} not available for {entity.kind}.')

    location_id = utility_data.get_location_id(location) if isinstance(location, str) else location
    data = handler(entity, location_id)

    if measure == 'estimate':
        value_cols, var_name = COVARIATE_VALUE_COLUMNS, 'parameter'
    elif measure == 'exposure_distribution_weights':
        value_cols, var_name = DISTRIBUTION_COLUMNS, 'parameter'
    else:
        value_cols, var_name = DRAW_COLUMNS, 'draw'

    data = utilities.reshape(data, value_cols=value_cols, var_name=var_name)

    return data


def get_raw_incidence(entity: Union[Cause, Sequela], location_id: int) -> pd.DataFrame:
    data = extract.extract_data(entity, 'incidence', location_id)
    if entity.kind == 'cause':
        restrictions_entity = entity
    else:  # sequela
        cause = [c for c in causes if c.sequelae and entity in c.sequelae][0]
        restrictions_entity = cause

    data = utilities.filter_data_by_restrictions(data, restrictions_entity,
                                                 'yld', utility_data.get_age_group_ids())
    data = utilities.normalize(data, fill_value=0)
    data = data.filter(DEMOGRAPHIC_COLUMNS + DRAW_COLUMNS)
    return data


def get_incidence(entity: Union[Cause, Sequela], location_id: int) -> pd.DataFrame:
    data = get_data(entity, 'raw_incidence', location_id)
    data = data.set_index(utilities.get_ordered_index_cols(data.columns.difference({'value'})))
    prevalence = get_data(entity, 'prevalence', location_id)
    prevalence = prevalence.set_index(utilities.get_ordered_index_cols(prevalence.columns.difference({'value'})))
    # Convert from "True incidence" to the incidence rate among susceptibles
    data /= 1 - prevalence
    return data.fillna(0).reset_index()


def get_prevalence(entity: Union[Cause, Sequela], location_id: int) -> pd.DataFrame:
    data = extract.extract_data(entity, 'prevalence', location_id)
    if entity.kind == 'cause':
        restrictions_entity = entity
    else:  # sequela
        cause = [c for c in causes if c.sequelae and entity in c.sequelae][0]
        restrictions_entity = cause

    data = utilities.filter_data_by_restrictions(data, restrictions_entity,
                                                 'yld', utility_data.get_age_group_ids())
    data = utilities.normalize(data, fill_value=0)
    data = data.filter(DEMOGRAPHIC_COLUMNS + DRAW_COLUMNS)
    return data


def get_birth_prevalence(entity: Union[Cause, Sequela], location_id: int) -> pd.DataFrame:
    data = extract.extract_data(entity, 'birth_prevalence', location_id)
    data = data.filter(['year_id', 'sex_id', 'location_id'] + DRAW_COLUMNS)
    data = utilities.normalize(data, fill_value=0)
    return data


def get_disability_weight(entity: Union[Cause, Sequela], location_id: int) -> pd.DataFrame:
    if entity.kind == 'cause':
        data = utility_data.get_demographic_dimensions(location_id, draws=True, value=0.0)
        data = data.set_index(utilities.get_ordered_index_cols(data.columns.difference({'value'})))
        if entity.sequelae:
            for sequela in entity.sequelae:
                try:
                    prevalence = get_data(sequela, 'prevalence', location_id)
                    prevalence = prevalence.set_index(
                        utilities.get_ordered_index_cols(prevalence.columns.difference({'value'})))
                except DataDoesNotExistError:
                    # sequela prevalence does not exist so no point continuing with this sequela
                    continue
                disability = get_data(sequela, 'disability_weight', location_id)
                disability['location_id'] = location_id
                disability = disability.set_index(
                    utilities.get_ordered_index_cols(disability.columns.difference({'value'})))
                data += prevalence * disability
        cause_prevalence = get_data(entity, 'prevalence', location_id)
        cause_prevalence = cause_prevalence.set_index(
            utilities.get_ordered_index_cols(cause_prevalence.columns.difference({'value'})))
        data = (data / cause_prevalence).fillna(0).reset_index()
    else:  # entity.kind == 'sequela'
        if not entity.healthstate.disability_weight_exists:
            data = utility_data.get_demographic_dimensions(location_id, draws=True, value=0.0)
        else:
            data = extract.extract_data(entity, 'disability_weight', location_id)
            data = utilities.normalize(data)
            cause = [c for c in causes if c.sequelae and entity in c.sequelae][0]
            data = utilities.clear_disability_weight_outside_restrictions(data, cause, 0.0,
                                                                          utility_data.get_age_group_ids())
            data = data.filter(DEMOGRAPHIC_COLUMNS + DRAW_COLUMNS)

    return data


def get_remission(entity: Cause, location_id: int) -> pd.DataFrame:
    data = extract.extract_data(entity, 'remission', location_id)

    data = utilities.filter_data_by_restrictions(data, entity,
                                                 'yld', utility_data.get_age_group_ids())
    data = utilities.normalize(data, fill_value=0)
    data = data.filter(DEMOGRAPHIC_COLUMNS + DRAW_COLUMNS)
    return data


def get_cause_specific_mortality(entity: Cause, location_id: int) -> pd.DataFrame:
    deaths = get_data(entity, 'deaths', location_id)
    deaths = deaths.set_index(utilities.get_ordered_index_cols(deaths.columns.difference({'draw', 'value'})))
    pop = get_data(Population(), 'structure', location_id)
    pop = pop.set_index(utilities.get_ordered_index_cols(pop.columns.difference({'value'})))
    data = deaths.join(pop, lsuffix='_deaths', rsuffix='_pop')
    data['value'] = data['value_deaths'].divide(data['value_pop'])
    return data.drop(['value_deaths', 'value_pop'], 'columns').reset_index()


def get_excess_mortality(entity: Cause, location_id: int) -> pd.DataFrame:
    csmr = get_data(entity, 'cause_specific_mortality', location_id)
    csmr = csmr.set_index(utilities.get_ordered_index_cols(csmr.columns.difference({'value'})))
    prevalence = get_data(entity, 'prevalence', location_id)
    prevalence = prevalence.set_index(utilities.get_ordered_index_cols(prevalence.columns.difference({'value'})))
    data = (csmr / prevalence).fillna(0)
    data = data.replace([np.inf, -np.inf], 0)
    return data.reset_index()


def get_case_fatality(entity: Cause, location_id: int):
    raise NotImplementedError()


def get_deaths(entity: Cause, location_id: int) -> pd.DataFrame:
    data = extract.extract_data(entity, 'deaths', location_id)
    data = utilities.filter_data_by_restrictions(data, entity,
                                                 'yll', utility_data.get_age_group_ids())
    data = utilities.normalize(data, fill_value=0)
    data = data.filter(DEMOGRAPHIC_COLUMNS + DRAW_COLUMNS)
    return data


def get_exposure(entity: Union[RiskFactor, AlternativeRiskFactor, CoverageGap], location_id: int) -> pd.DataFrame:
    data = extract.extract_data(entity, 'exposure', location_id)
    data = data.drop('modelable_entity_id', 'columns')

    if entity.name in EXTRA_RESIDUAL_CATEGORY:
        data = data[data.parameter != EXTRA_RESIDUAL_CATEGORY[entity.name]]

    if entity.kind in ['risk_factor', 'alternative_risk_factor']:
        data = utilities.filter_data_by_restrictions(data, entity,
                                                     'outer', utility_data.get_age_group_ids())

    if entity.distribution in ['dichotomous', 'ordered_polytomous', 'unordered_polytomous']:
        tmrel_cat = sorted(list(entity.categories.to_dict()), key=lambda x: int(x[3:]))[-1]
        exposed = data[data.parameter != tmrel_cat]
        unexposed = data[data.parameter == tmrel_cat]

        #  FIXME: We fill 1 as exposure of tmrel category, which is not correct.
        data = pd.concat([utilities.normalize(exposed, fill_value=0), utilities.normalize(unexposed, fill_value=1)],
                         ignore_index=True)

        # normalize so all categories sum to 1
        cols = list(set(data.columns).difference(DRAW_COLUMNS + ['parameter']))
        sums = data.groupby(cols)[DRAW_COLUMNS].sum()
        data = (data.groupby('parameter')
                .apply(lambda df: df.set_index(cols).loc[:, DRAW_COLUMNS].divide(sums))
                .reset_index())
    else:
        data = utilities.normalize(data, fill_value=0)
    data = data.filter(DEMOGRAPHIC_COLUMNS + DRAW_COLUMNS + ['parameter'])
    return data


def get_exposure_standard_deviation(entity: Union[RiskFactor, AlternativeRiskFactor], location_id: int) -> pd.DataFrame:
    data = extract.extract_data(entity, 'exposure_standard_deviation', location_id)
    data = data.drop('modelable_entity_id', 'columns')

    exposure = extract.extract_data(entity, 'exposure', location_id)
    valid_age_groups = utilities.get_exposure_and_restriction_ages(exposure, entity)
    data = data[data.age_group_id.isin(valid_age_groups)]

    data = utilities.normalize(data, fill_value=0)
    data = data.filter(DEMOGRAPHIC_COLUMNS + DRAW_COLUMNS)
    return data


def get_exposure_distribution_weights(entity: Union[RiskFactor, AlternativeRiskFactor], location_id: int) -> pd.DataFrame:
    data = extract.extract_data(entity, 'exposure_distribution_weights', location_id)

    exposure = extract.extract_data(entity, 'exposure', location_id)
    valid_ages = utilities.get_exposure_and_restriction_ages(exposure, entity)

    data.drop('age_group_id', axis=1, inplace=True)
    df = []
    for age_id in valid_ages:
        copied = data.copy()
        copied['age_group_id'] = age_id
        df.append(copied)
    data = pd.concat(df)
    data = utilities.normalize(data, fill_value=0, cols_to_fill=DISTRIBUTION_COLUMNS)
    data = data.filter(['location_id', 'sex_id', 'age_group_id', 'year_id'] + DISTRIBUTION_COLUMNS)
    return data


def filter_relative_risk_to_cause_restrictions(data: pd.DataFrame) -> pd.DataFrame:
    """ It applies age restrictions according to affected causes
    and affected measures. If affected measure is incidence_rate,
    it applies the yld_age_restrictions. If affected measure is
    excess_mortality, it applies the yll_age_restrictions to filter
    the relative_risk data"""

    causes_map = {c.name: c for c in causes}
    temp = []
    affected_entities = set(data.affected_entity)
    affected_measures = set(data.affected_measure)
    for cause, measure in product(affected_entities, affected_measures):
        df = data[(data.affected_entity == cause) & (data.affected_measure == measure)]
        cause = causes_map[cause]
        if measure == 'excess_mortality':
            start, end = utilities.get_age_group_ids_by_restriction(cause, 'yll')
        else:  # incidence_rate
            start, end = utilities.get_age_group_ids_by_restriction(cause, 'yld')
        temp.append(df[df.age_group_id.isin(range(start, end + 1))])
    data = pd.concat(temp)
    return data


def get_relative_risk(entity: Union[RiskFactor, CoverageGap], location_id: int) -> pd.DataFrame:
    data = extract.extract_data(entity, 'relative_risk', location_id)

    if entity.kind == 'risk_factor':
        # FIXME: we don't currently support yll-only causes so I'm dropping them because the data in some cases is
        #  very messed up, with mort = morb = 1 (e.g., aortic aneurysm in the RR data for high systolic bp) -
        #  2/8/19 K.W.
        yll_only_causes = set([c.gbd_id for c in causes if c.restrictions.yll_only])
        data = data[~data.cause_id.isin(yll_only_causes)]

        data = utilities.convert_affected_entity(data, 'cause_id')
        morbidity = data.morbidity == 1
        mortality = data.mortality == 1
        data.loc[morbidity & mortality, 'affected_measure'] = 'incidence_rate'
        data.loc[morbidity & ~mortality, 'affected_measure'] = 'incidence_rate'
        data.loc[~morbidity & mortality, 'affected_measure'] = 'excess_mortality'
        data = filter_relative_risk_to_cause_restrictions(data)
    else:  # coverage_gap
        data = utilities.convert_affected_entity(data, 'rei_id')
        data['affected_measure'] = 'exposure_parameters'

    result = []
    for affected_entity in data.affected_entity.unique():
        df = data[data.affected_entity == affected_entity]
        df = (df.groupby('parameter')
              .apply(utilities.normalize, fill_value=1)
              .reset_index(drop=True))
        result.append(df)
    data = pd.concat(result)
    data = data.filter(DEMOGRAPHIC_COLUMNS + ['affected_entity', 'affected_measure', 'parameter'] + DRAW_COLUMNS)

    if entity.distribution in ['dichotomous', 'ordered_polytomous', 'unordered_polytomous']:
        tmrel_cat = sorted(list(entity.categories.to_dict()), key=lambda x: int(x[3:]))[-1]
        tmrel_data = data.loc[data.parameter == tmrel_cat]
        tmrel_data[DRAW_COLUMNS] = tmrel_data[DRAW_COLUMNS].mask(np.isclose(tmrel_data[DRAW_COLUMNS], 1.0), 1.0)
        data = pd.concat([tmrel_data, data.loc[data.parameter != tmrel_cat]])

    return data


def filter_by_relative_risk(df: pd.DataFrame, relative_risk: pd.DataFrame) -> pd.DataFrame:
    c_id = df.cause_id.unique()[0]
    rr = relative_risk[relative_risk.cause_id == c_id]
    #  We presume all attributable mortality moves through incidence.
    if set(rr.mortality) == {1} and set(rr.morbidity) == {1}:
        df = df[df.measure_id == MEASURES['YLDs']]
    return df


def get_population_attributable_fraction(entity: Union[RiskFactor, Etiology], location_id: int) -> pd.DataFrame:
    causes_map = {c.gbd_id: c for c in causes}
    if entity.kind == 'risk_factor':
        data = extract.extract_data(entity, 'population_attributable_fraction', location_id)
        relative_risk = extract.extract_data(entity, 'relative_risk', location_id)

        # FIXME: we don't currently support yll-only causes so I'm dropping them because the data in some cases is
        #  very messed up, with mort = morb = 1 (e.g., aortic aneurysm in the RR data for high systolic bp) -
        #  2/8/19 K.W.
        yll_only_causes = set([c.gbd_id for c in causes if c.restrictions.yll_only])
        data = data[~data.cause_id.isin(yll_only_causes)]
        relative_risk = relative_risk[~relative_risk.cause_id.isin(yll_only_causes)]

        data = (data.groupby('cause_id', as_index=False)
                .apply(filter_by_relative_risk, relative_risk)
                .reset_index(drop=True))

        temp = []
        # We filter paf age groups by cause level restrictions.
        for (c_id, measure), df in data.groupby(['cause_id', 'measure_id']):
            cause = causes_map[c_id]
            measure = 'yll' if measure == MEASURES['YLLs'] else 'yld'
            df = utilities.filter_data_by_restrictions(df, cause, measure, utility_data.get_age_group_ids())
            temp.append(df)
        data = pd.concat(temp, ignore_index=True)

    else:  # etiology
        data = extract.extract_data(entity, 'etiology_population_attributable_fraction', location_id)
        cause = [c for c in causes if entity in c.etiologies][0]
        data = utilities.filter_data_by_restrictions(data, cause, 'inner', utility_data.get_age_group_ids())
        if np.any(data[DRAW_COLUMNS] < 0):
            warnings.warn(f"{entity.name.capitalize()} has negative values for paf. These will be replaced with 0.")
            other_cols = [c for c in data.columns if c not in DRAW_COLUMNS]
            data.set_index(other_cols, inplace=True)
            data = data.where(data[DRAW_COLUMNS] > 0, 0).reset_index()

    data = utilities.convert_affected_entity(data, 'cause_id')
    data.loc[data['measure_id'] == MEASURES['YLLs'], 'affected_measure'] = 'excess_mortality'
    data.loc[data['measure_id'] == MEASURES['YLDs'], 'affected_measure'] = 'incidence_rate'
    data = (data.groupby(['affected_entity', 'affected_measure'])
            .apply(utilities.normalize, fill_value=0)
            .reset_index(drop=True))
    data = data.filter(DEMOGRAPHIC_COLUMNS + ['affected_entity', 'affected_measure'] + DRAW_COLUMNS)
    return data


def get_mediation_factors(entity, location_id):
    raise NotImplementedError()


def get_estimate(entity: Covariate, location_id: int) -> pd.DataFrame:
    data = extract.extract_data(entity, 'estimate', location_id)

    key_columns = ['location_id', 'year_id']
    if entity.by_age:
        key_columns.append('age_group_id')
    if entity.by_sex:
        key_columns.append('sex_id')

    data = data.filter(key_columns + COVARIATE_VALUE_COLUMNS)
    data = utilities.normalize(data)
    return data


def get_cost(entity: Union[HealthcareEntity, HealthTechnology], location_id: int) -> pd.DataFrame:
    data = extract.extract_data(entity, 'cost', location_id)
    data = utilities.normalize(data, fill_value=0)
    data = data.filter(DEMOGRAPHIC_COLUMNS + [entity.kind] + DRAW_COLUMNS)
    return data


def get_utilization(entity: HealthcareEntity, location_id: int) -> pd.DataFrame:
    data = extract.extract_data(entity, 'utilization', location_id)
    data = utilities.normalize(data, fill_value=0)
    data = data.filter(DEMOGRAPHIC_COLUMNS + DRAW_COLUMNS)
    return data


def get_structure(entity: Population, location_id: int) -> pd.DataFrame:
    data = extract.extract_data(entity, 'structure', location_id)
    data = data.drop('run_id', 'columns').rename(columns={'population': 'value'})
    data = utilities.normalize(data)
    return data


def get_theoretical_minimum_risk_life_expectancy(entity: Population, location_id: int) -> pd.DataFrame:
    data = extract.extract_data(entity, 'theoretical_minimum_risk_life_expectancy', location_id)
    data = data.rename(columns={'age': 'age_group_start', 'life_expectancy': 'value'})
    data['age_group_end'] = data.age_group_start.shift(-1).fillna(125.)
    return data


def get_age_bins(entity: Population, location_id: int) -> pd.DataFrame:
    age_bins = utility_data.get_age_bins()[['age_group_name', 'age_group_start', 'age_group_end']]
    return age_bins


def get_demographic_dimensions(entity: Population, location_id: int) -> pd.DataFrame:
    demographic_dimensions = utility_data.get_demographic_dimensions(location_id)
    demographic_dimensions = utilities.normalize(demographic_dimensions)
    return demographic_dimensions
