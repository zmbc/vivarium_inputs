import operator
from typing import NamedTuple, Union, List
import warnings

import pandas as pd
import numpy as np

from gbd_mapping import (ModelableEntity, Cause, Sequela, RiskFactor,
                         Etiology, Covariate, CoverageGap)

from vivarium_inputs.globals import (DRAW_COLUMNS, DEMOGRAPHIC_COLUMNS, METRICS, MEASURES,
                                     DataAbnormalError, InvalidQueryError, DataNotExistError, gbd)
from vivarium_inputs.mapping_extension import AlternativeRiskFactor, HealthcareEntity, HealthTechnology


MAX_INCIDENCE = 10
MAX_REMISSION = 365/3
MAX_CATEG_REL_RISK = 15
MAX_CONT_REL_RISK = 5
MAX_UTILIZATION = 20
MAX_LIFE_EXP = 90


def check_metadata(entity: Union[ModelableEntity, NamedTuple], measure: str):
    metadata_checkers = {
        'sequela': check_sequela_metadata,
        'cause': check_cause_metadata,
        'risk_factor': check_risk_factor_metadata,
        'etiology': check_etiology_metadata,
        'covariate': check_covariate_metadata,
        'coverage_gap': check_coverage_gap_metadata,
        'health_technology': check_health_technology_metadata,
        'healthcare_entity': check_healthcare_entity_metadata,
        'population': check_population_metadata,
        'alternative_risk_factor': check_alternative_risk_factor_metadata,
    }

    metadata_checkers[entity.kind](entity, measure)


def validate_raw_data(data, entity, measure, location_id):
    validators = {
        # Cause-like measures
        'incidence': _validate_incidence,
        'prevalence': _validate_prevalence,
        'birth_prevalence': _validate_birth_prevalence,
        'disability_weight': _validate_disability_weight,
        'remission': _validate_remission,
        'deaths': _validate_deaths,
        # Risk-like measures
        'exposure': _validate_exposure,
        'exposure_standard_deviation': _validate_exposure_standard_deviation,
        'exposure_distribution_weights': _validate_exposure_distribution_weights,
        'relative_risk': _validate_relative_risk,
        'population_attributable_fraction': _validate_population_attributable_fraction,
        'mediation_factors': _validate_mediation_factors,
        # Covariate measures
        'estimate': _validate_estimate,
        # Health system measures
        'cost': _validate_cost,
        'utilization': _validate_utilization,
        # Population measures
        'structure': _validate_structure,
        'theoretical_minimum_risk_life_expectancy': _validate_theoretical_minimum_risk_life_expectancy,
    }

    if measure not in validators:
        raise NotImplementedError()

    validators[measure](data, entity, location_id)


def check_sequela_metadata(entity: Sequela, measure: str):
    if measure in ['incidence', 'prevalence', 'birth_prevalence']:
        _check_exists_in_range(entity, measure)
    else:  # measure == 'disability_weight
        if not entity.healthstate[f'{measure}_exists']:
            # throw warning so won't break if pulled for a cause where not all sequelae may be missing dws
            warnings.warn(f'Sequela {entity.name} does not have {measure} data.')


def check_cause_metadata(entity: Cause, measure: str):
    _check_exists_in_range(entity, measure)

    _warn_violated_restrictions(entity, measure)

    if measure != 'remission':
        consistent = entity[f"{measure}_consistent"]
        children = "subcauses" if measure == "deaths" else "sequela"

        if consistent is not None and not consistent:
            warnings.warn(f"{measure.capitalize()} data for cause {entity.name} may not exist for {children} in all "
                          f"locations. {children.capitalize()} models may not be consistent with models for this cause.")

        if consistent and not entity[f"{measure}_aggregates"]:
            warnings.warn(f"{children.capitalize()} {measure} data for cause {entity.name} may not correctly "
                          f"aggregate up to the cause level in all locations. {children.capitalize()} models may not "
                          f"be consistent with models for this cause.")


def check_risk_factor_metadata(entity: Union[AlternativeRiskFactor, RiskFactor], measure: str):
    if measure in ('exposure_distribution_weights', 'mediation_factors'):
        # we don't have any applicable metadata to check
        pass

    if entity.distribution == 'custom':
        raise NotImplementedError('We do not currently support risk factors with custom distributions.')

    if measure == 'population_attributable_fraction':
        _check_paf_types(entity)
    else:
        _check_exists_in_range(entity, measure)

        if measure == 'exposure' and entity.exposure_year_type in ('mix', 'incomplete'):
            warnings.warn(f'{measure.capitalize()} data for risk factor {entity.name} may contain unexpected '
                          f'or missing years.')

    _warn_violated_restrictions(entity, measure)


def check_alternative_risk_factor_metadata(entity: AlternativeRiskFactor, measure: str):
    pass


def check_etiology_metadata(entity: Etiology, measure: str):
    _check_paf_types(entity)


def check_covariate_metadata(entity: Covariate, measure: str):
    if not entity.mean_value_exists:
        warnings.warn(f'{measure.capitalize()} data for covariate {entity.name} may not contain'
                      f'mean values for all locations.')

    if not entity.uncertainty_exists:
        warnings.warn(f'{measure.capitalize()} data for covariate {entity.name} may not contain '
                      f'uncertainty values for all locations.')

    violated_restrictions = [f'by {r}' for r in ['sex', 'age'] if entity[f'by_{r}_violated']]
    if violated_restrictions:
        warnings.warn(f'Covariate {entity.name} may violate the following '
                      f'restrictions: {", ".join(violated_restrictions)}.')


def check_coverage_gap_metadata(entity: CoverageGap, measure: str):
    pass


def check_health_technology_metadata(entity: HealthTechnology, measure: str):
    raise NotImplementedError()


def check_healthcare_entity_metadata(entity: HealthcareEntity, measure: str):
    raise NotImplementedError()


def check_population_metadata(entity: NamedTuple, measure: str):
    pass


def _validate_incidence(data: pd.DataFrame, entity: Union[Cause, Sequela], location_id: int):
    check_data_exist(data, zeros_missing=True)

    expected_columns = ['measure_id', 'metric_id', f'{entity.kind}_id'] + DRAW_COLUMNS + DEMOGRAPHIC_COLUMNS
    check_columns(expected_columns, data.columns)

    check_measure_id(data, ['incidence'])
    check_metric_id(data, 'rate')

    if entity.kind == 'cause':
        check_age_group_ids(data, entity.restrictions.yld_age_group_id_start, entity.restrictions.yld_age_group_id_end)
    else:   # sequelae don't have restrictions
        check_age_group_ids(data)

    # como should return all sexes regardless of restrictions
    check_sex_ids(data, male_expected=True, female_expected=True)

    check_years(data, 'annual')
    check_location(data, location_id)

    if entity.kind == 'cause':
        check_age_restrictions(data, entity.restrictions.yld_age_group_id_start,
                               entity.restrictions.yld_age_group_id_end)
        check_sex_restrictions(data, entity.restrictions.male_only, entity.restrictions.female_only)

    check_value_columns_boundary(data, 0, 'lower', inclusive=True, error=True)
    check_value_columns_boundary(data, MAX_INCIDENCE, 'upper', value_columns=DRAW_COLUMNS, inclusive=True, error=False)


def _validate_prevalence(data: pd.DataFrame, entity: Union[Cause, Sequela], location_id: int):
    check_data_exist(data, zeros_missing=True)

    expected_columns = ['measure_id', 'metric_id', f'{entity.kind}_id'] + DRAW_COLUMNS + DEMOGRAPHIC_COLUMNS
    check_columns(expected_columns, data.columns)

    check_measure_id(data, ['prevalence'])
    check_metric_id(data, 'rate')

    if entity.kind == 'cause':
        check_age_group_ids(data, entity.restrictions.yld_age_group_id_start, entity.restrictions.yld_age_group_id_end)
    else:   # sequelae don't have restrictions
        check_age_group_ids(data)

    # como should all sexes regardless of restrictions
    check_sex_ids(data, male_expected=True, female_expected=True)

    check_years(data, 'annual')
    check_location(data, location_id)

    if entity.kind == 'cause':
        check_age_restrictions(data, entity.restrictions.yld_age_group_id_start,
                               entity.restrictions.yld_age_group_id_end)
        check_sex_restrictions(data, entity.restrictions.male_only, entity.restrictions.female_only)

    check_value_columns_boundary(data, 0, 'lower', value_columns=DRAW_COLUMNS, inclusive=True, error=True)
    check_value_columns_boundary(data, 1, 'upper', value_columns=DRAW_COLUMNS, inclusive=True, error=True)


def _validate_birth_prevalence(data: pd.DataFrame, entity: Union[Cause, Sequela], location_id: int):
    check_data_exist(data, zeros_missing=True)

    expected_columns = ['measure_id', 'metric_id', f'{entity.kind}_id'] + DRAW_COLUMNS + DEMOGRAPHIC_COLUMNS
    check_columns(expected_columns, data.columns)

    check_measure_id(data, ['incidence'])
    check_metric_id(data, 'rate')

    birth_age_group_id = 164
    if data.age_group_id.unique() != birth_age_group_id:
        raise DataAbnormalError(f'Birth prevalence data for {entity.kind} {entity.name} includes age groups beyond '
                                f'the expected birth age group (id {birth_age_group_id}.')

    # como should return all sexes regardless of restrictions
    check_sex_ids(data, male_expected=True, female_expected=True)

    check_years(data, 'annual')
    check_location(data, location_id)

    check_value_columns_boundary(data, 0, 'lower', value_columns=DRAW_COLUMNS, inclusive=True, error=True)
    check_value_columns_boundary(data, 1, 'upper', value_columns=DRAW_COLUMNS, inclusive=True, error=True)

    if entity.kind == 'cause':
        check_sex_restrictions(data, entity.restrictions.male_only, entity.restrictions.female_only)


def _validate_disability_weight(data: pd.DataFrame, entity: Sequela, location_id: int):
    check_data_exist(data, zeros_missing=False)

    expected_columns = ['location_id', 'age_group_id', 'sex_id', 'measure',
                        'healthstate', 'healthstate_id'] + DRAW_COLUMNS
    check_columns(expected_columns, data.columns)

    all_ages_age_group_id = 22
    if data.age_group_id.unique() != all_ages_age_group_id:
        raise DataAbnormalError(f'Disability weight data for {entity.kind} {entity.name} includes age groups beyond '
                                f'the expected all ages age group (id {all_ages_age_group_id}.')

    check_sex_ids(data, male_expected=False, female_expected=False, combined_expected=True)

    check_location(data, location_id)

    check_value_columns_boundary(data, 0, 'lower', value_columns=DRAW_COLUMNS, inclusive=True, error=True)
    check_value_columns_boundary(data, 1, 'upper', value_columns=DRAW_COLUMNS, inclusive=True, error=True)


def _validate_remission(data: pd.DataFrame, entity: Cause, location_id: int):
    check_data_exist(data, zeros_missing=True)

    expected_columns = ['measure_id', 'metric_id', 'model_version_id',
                        'modelable_entity_id'] + DEMOGRAPHIC_COLUMNS + DRAW_COLUMNS
    check_columns(expected_columns, data.columns)

    check_measure_id(data, ['remission'])
    check_metric_id(data, 'rate')

    restrictions = entity.restrictions

    check_age_group_ids(data, restrictions.yll_age_group_id_start, restrictions.yll_age_group_id_end)

    male_expected = restrictions.male_only or (not restrictions.male_only and not restrictions.female_only)
    female_expected = restrictions.female_only or (not restrictions.male_only and not restrictions.female_only)
    check_sex_ids(data, male_expected, female_expected)

    check_years(data, 'binned')
    check_location(data, location_id)

    check_age_restrictions(data, restrictions.yld_age_group_id_start, restrictions.yld_age_group_id_end)
    check_sex_restrictions(data, restrictions.male_only, restrictions.female_only)

    check_value_columns_boundary(data, 0, 'lower', value_columns=DRAW_COLUMNS, inclusive=True, error=True)
    check_value_columns_boundary(data, MAX_REMISSION, 'upper', value_columns=DRAW_COLUMNS, inclusive=True, error=False)


def _validate_deaths(data: pd.DataFrame, entity: Cause, location_id: int):
    check_data_exist(data, zeros_missing=True)

    expected_columns = ['measure_id', f'{entity.kind}_id', 'metric_id'] + DEMOGRAPHIC_COLUMNS + DRAW_COLUMNS
    check_columns(expected_columns, data.columns)

    check_measure_id(data, ['deaths'])
    check_metric_id(data, 'number')

    restrictions = entity.restrictions

    check_age_group_ids(data, restrictions.yll_age_group_id_start, restrictions.yll_age_group_id_end)

    male_expected = restrictions.male_only or (not restrictions.male_only and not restrictions.female_only)
    female_expected = restrictions.female_only or (not restrictions.male_only and not restrictions.female_only)
    check_sex_ids(data, male_expected, female_expected)

    check_years(data, 'annual')
    check_location(data, location_id)

    check_age_restrictions(data, restrictions.yll_age_group_id_start, restrictions.yll_age_group_id_end)
    check_sex_restrictions(data, restrictions.male_only, restrictions.female_only)

    check_value_columns_boundary(data, 0, 'lower', value_columns=DRAW_COLUMNS, inclusive=True, error=True)
    pop = gbd.get_population(location_id)
    idx_cols = ['age_group_id', 'year_id', 'sex_id']
    pop = pop[(pop.year_id.isin(data.year_id.unique())) & (pop.sex_id != gbd.COMBINED[0])].set_index(idx_cols).population
    check_value_columns_boundary(data.set_index(idx_cols), pop, 'upper',
                                 value_columns=DRAW_COLUMNS, inclusive=True, error=False)


def _validate_exposure(data: pd.DataFrame, entity: Union[RiskFactor, CoverageGap], location_id: int):
    check_data_exist(data, zeros_missing=True)

    expected_columns = ['rei_id', 'modelable_entity_id', 'parameter',
                        'measure_id', 'metric_id'] + DEMOGRAPHIC_COLUMNS + DRAW_COLUMNS
    check_columns(expected_columns, data.columns)

    check_measure_id(data,  ['prevalence', 'proportion', 'continuous'])
    check_metric_id(data, 'rate')

    cats = data.groupby('parameter')

    age_start, age_end, male_expected, female_expected = None, None, True, True

    if entity.kind == 'risk_factor':
        restrictions = entity.restrictions
        age_start = min(restrictions.yld_age_group_id_start, restrictions.yll_age_group_id_start)
        age_end = max(restrictions.yld_age_group_id_end, restrictions.yll_age_group_id_end)
        male_expected = restrictions.male_only or (not restrictions.male_only and not restrictions.female_only)
        female_expected = restrictions.female_only or (not restrictions.male_only and not restrictions.female_only)

    cats.apply(check_age_group_ids, age_start, age_end)
    cats.apply(check_sex_ids, male_expected, female_expected)

    if not check_years(data, 'annual', error=False) and not check_years(data, 'binned', error=False):
        raise DataAbnormalError(f'Exposure data for {entity.kind} {entity.name} contains a year range '
                                f'that is neither annual nor binned.')
    check_location(data, location_id)

    if entity.kind == 'risk_factor':
        cats.apply(check_age_restrictions, age_start, age_end)
        cats.apply(check_sex_restrictions, entity.restrictions.male_only, entity.restrictions.female_only)

    if entity.distribution in ('ensemble', 'lognormal', 'normal'):  # continuous
        if entity.tmred.inverted:
            check_value_columns_boundary(data, entity.tmred.max, 'upper',
                                         value_columns=DRAW_COLUMNS, inclusive=True, error=False)
        else:
            check_value_columns_boundary(data, entity.tmred.min, 'lower',
                                         value_columns=DRAW_COLUMNS, inclusive=True, error=False)
    else:  # categorical
        check_value_columns_boundary(data, 0, 'lower', value_columns=DRAW_COLUMNS, inclusive=True, error=True)
        check_value_columns_boundary(data, 1, 'upper', value_columns=DRAW_COLUMNS, inclusive=True, error=True)

        g = data.groupby(DEMOGRAPHIC_COLUMNS)[DRAW_COLUMNS].sum()
        if not np.all(g[DRAW_COLUMNS] == 1):
            raise DataAbnormalError(f'Exposure data for {entity.kind} {entity.name} '
                                    f'does not sum to 1 across all categories.')


def _validate_exposure_standard_deviation(data, entity, location_id):

    expected_columns = ('rei_id', 'modelable_entity_id', 'measure_id', 'metric_id') + DEMOGRAPHIC_COLUMNS + DRAW_COLUMNS
    check_columns(expected_columns, data.columns)
    check_location(data, location_id)


def _validate_exposure_distribution_weights(data, entity, location_id):
    key_cols = ['rei_id', 'location_id', 'sex_id', 'age_group_id', 'measure']
    distribution_cols = ['exp', 'gamma', 'invgamma', 'llogis', 'gumbel', 'invweibull', 'weibull',
                         'lnorm', 'norm', 'glnorm', 'betasr', 'mgamma', 'mgumbel']
    check_columns(key_cols + distribution_cols, data.columns)


def _validate_relative_risk(data, entity, location_id):
    expected_columns = ('rei_id', 'modelable_entity_id', 'cause_id', 'mortality',
                        'morbidity', 'metric_id', 'parameter') + DEMOGRAPHIC_COLUMNS + DRAW_COLUMNS
    check_columns(expected_columns, data.columns)
    check_years(data, 'binned')
    check_location(data, location_id)


def _validate_population_attributable_fraction(data, entity, location_id):
    expected_columns = ('metric_id', 'measure_id', 'rei_id', 'cause_id') + DRAW_COLUMNS + DEMOGRAPHIC_COLUMNS
    check_columns(expected_columns, data.columns)
    check_years(data, 'annual')
    check_location(data, location_id)


def _validate_mediation_factors(data, entity, location_id):
    raise NotImplementedError()


def _validate_estimate(data, entity, location_id):
    expected_columns = ['model_version_id', 'covariate_id', 'covariate_name_short', 'location_id',
                        'location_name', 'year_id', 'age_group_id', 'age_group_name', 'sex_id',
                        'sex', 'mean_value', 'lower_value', 'upper_value']
    check_columns(expected_columns, data.columns)
    check_years(data, 'annual')
    check_location(data, location_id)


def _validate_cost(data, entity, location_id):
    raise NotImplementedError()


def _validate_utilization(data, entity, location_id):
    raise NotImplementedError()


def _validate_structure(data, entity, location_id):
    expected_columns = ['age_group_id', 'location_id', 'year_id', 'sex_id', 'population', 'run_id']
    check_columns(expected_columns, data.columns)
    check_years(data, 'annual')
    check_location(data, location_id)


def _validate_theoretical_minimum_risk_life_expectancy(data, entity, location_id):
    pass


############################
# CHECK METADATA UTILITIES #
############################

def _check_exists_in_range(entity: Union[Sequela, Cause, RiskFactor], measure: str):
    exists = entity[f'{measure}_exists']
    if exists is None:
        raise InvalidQueryError(f'{measure.capitalize()} data is not expected to exist '
                                f'for {entity.kind} {entity.name}.')
    if not exists:
        warnings.warn(f'{measure.capitalize()} data for {entity.kind} {entity.name} may not exist for all locations.')
    if f'{measure}_in_range' in entity.__slots__ and exists and not entity[f'{measure}_in_range']:
        warnings.warn(f'{measure.capitalize()} for {entity.kind} {entity.name} may be outside the normal range.')


def _warn_violated_restrictions(entity, measure):
    violated_restrictions = [r.replace(f'by_{measure}', '').replace(measure, '').replace('_', ' ').replace(' violated', '')
                             for r in entity.restrictions.violated if measure in r]
    if violated_restrictions:
        warnings.warn(f'{entity.kind.capitalize()} {entity.name} {measure} data may violate the '
                      f'following restrictions: {", ".join(violated_restrictions)}.')


def _check_paf_types(entity):
    paf_types = np.array(['yll', 'yld'])
    missing_pafs = paf_types[[not entity.population_attributable_fraction_yll_exists,
                              not entity.population_attributable_fraction_yld_exists]]
    if missing_pafs.size:
        warnings.warn(f'Population attributable fraction data for {", ".join(missing_pafs)} for '
                      f'{entity.kind} {entity.name} may not exist for all locations.')

    abnormal_range = paf_types[[entity.population_attributable_fraction_yll_exists
                                and not entity.population_attributable_fraction_yll_in_range,
                                entity.population_attributable_fraction_yld_exists
                                and not entity.population_attributable_fraction_yld_in_range]]
    if abnormal_range.size:
        warnings.warn(f'Population attributable fraction data for {", ".join(abnormal_range)} for '
                      f'{entity.kind} {entity.name} may be outside expected range [0, 1].')


########################
# VALIDATION UTILITIES #
########################

def check_years(data: pd.DataFrame, year_type: str, error: bool = True):
    """Check that years in passed data match expected range based on type.

    Parameters
    ----------
    data
        Dataframe containing 'year_id' column.
    year_type
        String 'annual' or 'binned' indicating expected year range.

    Returns
    -------
    bool
        True if years in `data` match expected `year_type`, false otherwise.

    Raises
    ------
    DataAbnormalError
        If `error` is turned on and any expected years are not found in data or
        any extra years found and `year_type` is 'binned'.

    """
    years = {'annual': list(range(1990, 2018)), 'binned': gbd.get_estimation_years()}
    expected_years = years[year_type]
    if set(data.year_id.unique()) < set(expected_years):
        if error:
            raise DataAbnormalError(f'Data has missing years: {set(expected_years).difference(set(data.year_id))}.')
        return False
    # if is it annual, we expect to have extra years from some cases like codcorrect/covariate
    if year_type == 'binned' and set(data.year_id.unique()) > set(expected_years):
        if error:
            raise DataAbnormalError(f'Data has extra years: {set(data.year_id).difference(set(expected_years))}.')
        return False
    return True


def check_location(data: pd.DataFrame, location_id: int):
    """Check that data contains only a single unique location id and that that
    location id matches either the global location id or the
    requested `location_id`.

    Parameters
    ----------
    data
        Dataframe containing a 'location_id' column.
    location_id
        The requested location_id.

    Raises
    ------
    DataAbnormalError
        If data contains multiple location ids or a location id other than the
        global or requested location id.

    """
    if len(data['location_id'].unique()) > 1:
        raise DataAbnormalError(f'Data contains multiple location ids.')
    data_location_id = data['location_id'].unique()[0]
    global_loc_id = 1
    if data_location_id not in [global_loc_id, location_id]:
        raise DataAbnormalError(f'Data pulled for {location_id} actually has location id {data_location_id}.')


def check_columns(expected_cols: List, existing_cols: List):
    """Verify that the passed lists of columns match.

    Parameters
    ----------
    expected_cols
        List of column names expected.
    existing_cols
        List of column names actually found in data.

    Raises
    ------
    DataAbnormalError
        If `expected_cols` does not match `existing_cols`.

    """
    if set(existing_cols) < set(expected_cols):
        raise DataAbnormalError(f'Data is missing columns: {set(expected_cols).difference(set(existing_cols))}.')
    elif set(existing_cols) > set(expected_cols):
        raise DataAbnormalError(f'Data returned extra columns: {set(existing_cols).difference(set(expected_cols))}.')


def check_data_exist(data: pd.DataFrame, zeros_missing: bool = True,
                     value_columns: list = DRAW_COLUMNS, error: bool = True) -> bool:
    """Check that values in data exist and none are missing and, if
    `zeros_missing` is turned on, not all zero.

    Parameters
    ----------
    data
        Dataframe containing `value_columns`.
    zeros_missing
        Boolean indicating whether to treat all zeros in `value_columns` as
        missing or not.
    value_columns
        List of columns in `data` to check for missing values.
    error
        Boolean indicating whether or not to error if data is missing.

    Returns
    -------
    bool
        True if non-missing, non-infinite, non-zero (if zeros_missing) values
        exist in data, False otherwise.

    Raises
    -------
    DataNotExistError
        If error flag is set to true and data is empty or contains any NaN
        values in `value_columns`, or contains all zeros in `value_columns` and
        zeros_missing is True.

    """
    if (data.empty or np.any(pd.isnull(data[value_columns]))
            or (zeros_missing and np.all(data[value_columns] == 0)) or np.any(np.isinf(data[value_columns]))):
        if error:
            raise DataNotExistError('Data contains no non-missing, non-zero draw values.')
        return False
    return True


def _get_restriction_ages(start_id: Union[float, None], end_id: Union[float, None]) -> list:
    if start_id is None:
        return []

    gbd_age_ids = gbd.get_age_group_id()
    start_index = gbd_age_ids.index(start_id)
    end_index = gbd_age_ids.index(end_id)

    return gbd_age_ids[start_index:end_index+1]


def _check_continuity(data_ages: set, all_ages: set):
    """Make sure data_ages is contiguous block in all_ages."""
    data_ages = list(data_ages)
    all_ages = list(all_ages)
    all_ages.sort()
    data_ages.sort()
    if all_ages[all_ages.index(data_ages[0]):all_ages.index(data_ages[-1])+1] != data_ages:
        raise DataAbnormalError(f'Data contains a non-contiguous age groups: {data_ages}.')


def check_age_group_ids(data: pd.DataFrame, restriction_start: float = None, restriction_end: float = None):
    """Check the set of age_group_ids included in data pulled from GBD for
    the following conditions:

        - if data ages contain invalid age group ids, error.
        - if data ages are equal to the set of all GBD age groups or the set of
        age groups within restriction bounds (if restrictions apply), pass.
        - if data ages are not a contiguous block of GBD age groups, error.
        - if data ages are a proper subset of the set of restriction age groups
        or the restriction age groups are a proper subset of the data ages,
        warn.


    Parameters
    ----------
    data
        Dataframe pulled containing age_group_id column.
    restriction_start
        Age group id representing the start of the restriction range
        if applicable.
    restriction_end
        Age group id representing the end of the restriction range
        if applicable.

    Raises
    ------
    DataAbnormalError
        If age group ids contained in data aren't all valid GBD age group ids
        or they don't make up a contiguous block.

    """
    all_ages = set(gbd.get_age_group_id())
    restriction_ages = set(_get_restriction_ages(restriction_start, restriction_end))
    data_ages = set(data.age_group_id)

    invalid_ages = data_ages.difference(all_ages)
    if invalid_ages:
        raise DataAbnormalError(f'Data contains invalid age group ids: {invalid_ages}.')

    _check_continuity(data_ages, all_ages)

    if data_ages < restriction_ages:
        warnings.warn('Data does not contain all age groups in restriction range.')
    elif restriction_ages and restriction_ages < data_ages:
        warnings.warn('Data contains additional age groups beyond those specified by restriction range.')
    else:  # data_ages == restriction_ages
        pass


def check_sex_ids(data: pd.DataFrame, male_expected: bool = True, female_expected: bool = True,
                  combined_expected: bool = False):
    """Check whether the data contains valid GBD sex ids and whether the set of
    sex ids in the data matches the expected set.

    Parameters
    ----------
    data
        Dataframe containing a sex_id column.
    male_expected
        Boolean indicating whether the male sex id is expected in this data.
        For some data pulling tools, this may correspond to whether the entity
        the data describes has a male_only sex restriction.
    female_expected
        Boolean indicating whether the female sex id is expected in this data.
        For some data pulling tools, this may correspond to whether the entity
        the data describes has a female_only sex restriction.
    combined_expected
        Boolean indicating whether data is expected to include the
        combined sex id.

    Raises
    ------
    DataAbnormalError
        If data contains any sex ids that aren't valid GBD sex ids.

    """
    valid_sex_ids = gbd.MALE + gbd.FEMALE + gbd.COMBINED  # these are single-item lists
    gbd_sex_ids = set(np.array(valid_sex_ids)[[male_expected, female_expected, combined_expected]])
    data_sex_ids = set(data.sex_id)

    invalid_sex_ids = data_sex_ids.difference(set(valid_sex_ids))
    if invalid_sex_ids:
        raise DataAbnormalError(f'Data contains invalid sex ids: {invalid_sex_ids}.')

    extra_sex_ids = data_sex_ids.difference(gbd_sex_ids)
    if extra_sex_ids:
        warnings.warn(f'Data contains the following extra sex ids {extra_sex_ids}.')

    missing_sex_ids = set(gbd_sex_ids).difference(data_sex_ids)
    if missing_sex_ids:
        warnings.warn(f'Data is missing the following expected sex ids: {missing_sex_ids}.')


def check_age_restrictions(data: pd.DataFrame, age_group_id_start: int, age_group_id_end: int,
                           value_columns: list = DRAW_COLUMNS):
    """Check that all expected age groups between age_group_id_start and
    age_group_id_end, inclusive, and only those age groups, appear in data with
    non-missing values in `value_columns`.

    Parameters
    ----------
    data
        Dataframe containing an age_group_id column.
    age_group_id_start
        Lower boundary of age group ids expected in data, inclusive.
    age_group_id_end
        Upper boundary of age group ids expected in data, exclusive.
    value_columns
        List of columns to verify values are non-missing for expected age
        groups and missing for not expected age groups.

    Raises
    ------
    DataAbnormalError
        If any age group ids in the range
        [`age_group_id_start`, `age_group_id_end`] don't appear in the data or
        if any additional age group ids (with the exception of 235) appear in
        the data.

    """
    expected_gbd_age_ids = _get_restriction_ages(age_group_id_start, age_group_id_end)

    # age groups we expected in data but that are not
    missing_age_groups = set(expected_gbd_age_ids).difference(set(data.age_group_id))
    # age groups we do not expect in data but that are (allow 235 because of metadata oversight)
    extra_age_groups = set(data.age_group_id).difference(set(expected_gbd_age_ids)) - {235}

    if missing_age_groups:
        raise DataAbnormalError(f'Data was expected to contain all age groups between ids '
                                f'{age_group_id_start} and {age_group_id_end}, '
                                f'but was missing the following: {missing_age_groups}.')
    if extra_age_groups:
        # we treat all 0s as missing in accordance with gbd so if extra age groups have all 0 data, that's fine
        should_be_zero = data[data.age_group_id.isin(extra_age_groups)]
        if check_data_exist(should_be_zero, value_columns=value_columns, error=False):
            raise DataAbnormalError(f'Data was only expected to contain values for age groups between ids '
                                    f'{age_group_id_start} and {age_group_id_end} (with the possible addition of 235), '
                                    f'but also included values for age groups {extra_age_groups}.')

    # make sure we're not missing data for all ages in restrictions
    if not check_data_exist(data[data.age_group_id.isin(expected_gbd_age_ids)],
                            value_columns=value_columns, error=False):
        raise DataAbnormalError(f'Data is missing for all age groups within restriction range.')


def check_value_columns_boundary(data: pd.DataFrame, boundary_value: Union[float, pd.Series], boundary_type: str,
                                 value_columns: list = DRAW_COLUMNS, inclusive: bool = True, error: bool = False):
    """Check that all values in DRAW_COLUMNS in data are above or below given
    boundary_value.

    Parameters
    ----------
    data
        Dataframe containing `value_columns`.
    boundary_value
        Value against which `value_columns` values will be checked. May be a
        series of values with a matching index to data.
    boundary_type
        String 'upper' or 'lower' indicating whether `boundary_value` is upper
        or lower limit on `value_columns`.
    value_columns
        List of column names in `data`, the values of which should be checked
        against `boundary_value`.
    inclusive
        Boolean indicating whether `boundary_value` is inclusive or not.
    error
        Boolean indicating whether error (True) or warning (False) should be
        raised if values are found outside `boundary_value`.

    Raises
    -------
    DataAbnormalError
        If any values in `value_columns` are above/below `boundary_value`,
        depending on `boundary_type`, if `error` is turned on.
    """
    msg = f'Data contains values {"below" if boundary_type == "lower" else "above"} ' \
        f'{"or equal to " if not inclusive else ""}the expected boundary ' \
        f'value{"s" if isinstance(boundary_value, pd.Series) else f" ({boundary_value})"}.'

    if boundary_type == "lower":
        op = operator.ge if inclusive else operator.gt
        data_values = data[value_columns].min(axis=1)
    else:
        op = operator.le if inclusive else operator.lt
        data_values = data[value_columns].max(axis=1)

    if isinstance(boundary_value, pd.Series):
        data_values = data_values.sort_index()
        boundary_value = boundary_value.sort_index()

    if not np.all(op(data_values, boundary_value)):
        if error:
            raise DataAbnormalError(msg)
        else:
            warnings.warn(msg)


def check_sex_restrictions(data: pd.DataFrame, male_only: bool, female_only: bool,
                           value_columns: list = DRAW_COLUMNS):
    """Check that all expected sex ids based on restrictions, and only those
    sex ids, appear in data with non-missing values in `value_columns`.

    Parameters
    ----------
    data
        Dataframe contained sex_id column.
    male_only
        Boolean indicating whether data is restricted to male only estimates.
    female_only
        Boolean indicating whether data is restricted to female only estimates.
    value_columns
        List of columns to verify values are non-missing for expected sex
        ids and missing for not expected sex ids.

    Raises
    -------
    DataAbnormalError
        If data violates passed sex restrictions.
    """
    female, male, combined = gbd.FEMALE[0], gbd.MALE[0], gbd.COMBINED[0]

    if male_only:
        if not check_data_exist(data[data.sex_id == male], value_columns=value_columns, error=False):
            raise DataAbnormalError('Data is restricted to male only, but is missing data values for males.')

        if (set(data.sex_id) != {male} and
                check_data_exist(data[data.sex_id != male], value_columns=value_columns, error=False)):
            raise DataAbnormalError('Data is restricted to male only, but contains '
                                    'non-male sex ids for which data values are not all 0.')

    if female_only:
        if not check_data_exist(data[data.sex_id == female], value_columns=value_columns, error=False):
            raise DataAbnormalError('Data is restricted to female only, but is missing data values for females.')

        if (set(data.sex_id) != {female} and
                check_data_exist(data[data.sex_id != female], value_columns=value_columns, error=False)):
            raise DataAbnormalError('Data is restricted to female only, but contains '
                                    'non-female sex ids for which data values are not all 0.')

    if not male_only and not female_only:
        if {male, female}.issubset(set(data.sex_id)):
            if (not check_data_exist(data[data.sex_id == male], value_columns=value_columns, error=False) or
               not check_data_exist(data[data.sex_id == female], value_columns=value_columns, error=False)):
                raise DataAbnormalError('Data has no sex restrictions, but does not contain non-zero '
                                        'values for both males and females.')
        else:  # check combined sex id
            if not check_data_exist(data[data.sex_id == combined], value_columns=value_columns, error=False):
                raise DataAbnormalError('Data has no sex restrictions, but does not contain non-zero '
                                        'values for both males and females.')


def check_measure_id(data: pd.DataFrame, allowable_measures: List[str]):
    """Check that data contains only a single measure id and that it is one of
    the allowed measure ids.

    Parameters
    ----------
    data
        Dataframe containing 'measure_id' column.
    allowable_measures
        List of strings dictating the possible values for measure id when
        mapped via MEASURES.

    Raises
    ------
    DataAbnormalError
        If data contains multiple measure ids or a non-permissible measure id.
    """
    if len(set(data.measure_id)) > 1:
        raise DataAbnormalError(f'Data has multiple measure ids: {set(data.measure_id)}.')
    if not set(data.measure_id).issubset(set([MEASURES[m.capitalize()] for m in allowable_measures])):
        raise DataAbnormalError(f'Data includes a measure id not in the expected measure ids for this measure.')


def check_metric_id(data: pd.DataFrame, expected_metric: str):
    """Check that data contains only a single metric id and that it matches the
    expected metric.

    Parameters
    ----------
    data
        Dataframe containing 'metric_id' column.
    expected_metric
        String dictating the expected metric, the id of which can be found via
        METRICS.

    Raises
    ------
    DataAbnormalError
        If data contains any metric id other than the expected.

    """
    if set(data.metric_id) != {METRICS[expected_metric.capitalize()]}:
        raise DataAbnormalError(f'Data includes metrics beyond the expected {expected_metric.lower()} '
                                f'(metric_id {METRICS[expected_metric.capitalize()]}')



