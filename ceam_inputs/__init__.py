from vivarium.config_tree import ConfigTree  # Just for typing info.

# Make these top level imports until external references can be removed.
from ceam_inputs.gbd_mapping import *

from ceam_inputs import core, gbd
from ceam_inputs.util import get_input_config
from ceam_inputs.utilities import select_draw_data, get_age_group_midpoint_from_age_group_id, normalize_for_simulation


def _clean_and_filter_data(data, draw_number, column_name):
    key_cols = [c for c in data.columns if 'draw' not in c]
    data = data[key_cols + [f'draw_{draw_number}']]
    data = get_age_group_midpoint_from_age_group_id(data)
    return select_draw_data(data, draw_number, column_name)


####################################
# Measures for cause like entities #
####################################

def get_prevalence(entity, override_config=None):
    config = get_input_config(override_config)
    data = core.get_prevalence([entity], [config.input_data.location_id])
    return _clean_and_filter_data(data, config.run_configuration.input_draw_number, 'prevalence')


def get_incidence(entity, override_config: ConfigTree=None):
    config = get_input_config(override_config)
    data = core.get_incidence([entity], [config.input_data.location_id])
    return _clean_and_filter_data(data, config.run_configuration.input_draw_number, 'rate')


def get_remission(cause, override_config=None):
    config = get_input_config(override_config)
    data = core.get_remission([cause], [config.input_data.location_id])
    return _clean_and_filter_data(data, config.run_configuration.input_draw_number, 'rate')


def get_cause_specific_mortality(cause, override_config=None):
    config = get_input_config(override_config)
    data = core.get_cause_specific_mortality([cause], [config.input_data.location_id])
    return _clean_and_filter_data(data, config.run_configuration.input_draw_number, 'rate')


def get_excess_mortality(cause, override_config: ConfigTree=None):
    config = get_input_config(override_config)
    data = core.get_excess_mortality([cause], [config.input_data.location_id])
    return _clean_and_filter_data(data, config.run_configuration.input_draw_number, 'rate')


def get_disability_weight(sequela, override_config=None):
    config = get_input_config(override_config)
    data = core.get_disability_weight([sequela], [config.input_data.location_id])
    return float(data[f'draw_{config.run_configuration.input_draw_number}'])


####################################
# Measures for risk like entities  #
####################################


def get_relative_risk(entity, cause, override_config=None):
    config = get_input_config(override_config)
    data = core.get_relative_risk([entity], [config.input_data.location_id])
    data = data[data['cause_id'] == cause.gbd_id]
    return _clean_and_filter_data(data, config.run_configuration.input_draw_number, 'relative_risk')


def get_exposure(risk, override_config=None):
    config = get_input_config(override_config)
    data = core.get_exposure([risk], [config.input_data.location_id])
    data = _clean_and_filter_data(data, config.run_configuration.input_draw_number, 'mean')
    # FIXME: This is here because FPG puts zeros in its unmodelled age groups unlike most other gbd risks
    data = data[data['mean'] != 0]
    return data


def get_exposure_standard_deviation(risk, override_config=None):
    config = get_input_config(override_config)
    data = core.get_exposure_standard_deviation([risk], [config.input_data.location_id])
    data = _clean_and_filter_data(data, config.run_configuration.input_draw_number, 'standard_deviation')
    # FIXME: This is here because FPG puts zeros in its unmodelled age groups unlike most other gbd risks
    data = data[data['standard_deviation'] != 0]
    return data


def get_population_attributable_fraction(entity, cause, override_config=None):
    config = get_input_config(override_config)
    data = core.get_population_attributable_fraction([entity], [config.input_data.location_id])
    data = data[data['cause_id'] == cause.gbd_id]
    return _clean_and_filter_data(data, config.run_configuration.input_draw_number, 'population_attributable_fraction')


def get_ensemble_weights(risk, override_config=None):
    config = get_input_config(override_config)
    return core.get_ensemble_weights([risk], [config.input_data.location_id])


def get_mediation_factor(risk, cause, override_config=None):
    config = get_input_config(override_config)
    data = core.get_mediation_factor([risk], [config.input_data.location_id])
    try:
        return data[data['cause_id'] == cause.gbd_id] if cause.gbd_id in data['cause_id'] else 0
    except TypeError:
        return 0


def get_risk_correlation_matrix(override_config=None):
    config = get_input_config(override_config)
    data = core.get_risk_correlation_matrix([config.input_data.location_id])
    del data['location_id']
    return data

#######################
# Other kinds of data #
#######################


def get_populations(override_config=None, location=None):
    config = get_input_config(override_config)
    if location:
        data = core.get_populations([location])
    else:
        data = core.get_populations([config.input_data.location_id])
    data = get_age_group_midpoint_from_age_group_id(data)
    data = normalize_for_simulation(data)
    return data


def get_age_bins():
    return core.get_age_bins()


def get_theoretical_minimum_risk_life_expectancy():
    return core.get_theoretical_minimum_risk_life_expectancy()


def get_subregions(override_config=None):
    config = get_input_config(override_config)
    return core.get_subregions([config.input_data.location_id])


def get_outpatient_visit_costs(override_config=None):
    config = get_input_config(override_config)
    data = core.get_cost([healthcare_entities.outpatient_visits], [config.input_data.location_id])
    data = data[['year_id', f'draw_{config.run_configuration.input_draw_number}']]
    return data.rename(columns={'year_id':'year', f'draw_{config.run_configuration.input_draw_number}':'op_cost'})


def get_inpatient_visit_costs(override_config=None):
    config = get_input_config(override_config)
    data = core.get_cost([healthcare_entities.inpatient_visits], [config.input_data.location_id])
    data = data[['year_id', f'draw_{config.run_configuration.input_draw_number}']]
    return data.rename(columns={'year_id':'year', f'draw_{config.run_configuration.input_draw_number}':'ip_cost'})


def get_hypertension_drug_costs(override_config=None):
    config = get_input_config(override_config)
    return core.get_cost([treatment_technologies.hypertension_drugs], [config.input_data.location_id])


def get_age_specific_fertility_rates(override_config=None):
    config = get_input_config(override_config)
    data = core.get_covariate_estimates([covariates.age_specific_fertility_rate], [config.input_data.location_id])
    data = get_age_group_midpoint_from_age_group_id(data)
    data = normalize_for_simulation(data)
    return data.loc[data.sex == 'Female', ['age', 'year', 'mean_value']].rename(columns={'mean_value': 'rate'})


def get_live_births_by_sex(override_config=None):
    config = get_input_config(override_config)
    data = core.get_covariate_estimates([covariates.live_births_by_sex], [config.input_data.location_id])
    data = data[['sex_id', 'year_id', 'mean_value', 'lower_value', 'upper_value']]
    return normalize_for_simulation(data)


def get_dtp3_coverage(override_config=None):
    config = get_input_config(override_config)
    data = core.get_covariate_estimates([covariates.dtp3_coverage_proportion], [config.input_data.location_id])
    data = normalize_for_simulation(data)
    return data[['mean_value', 'lower_value', 'upper_value', 'year']]


def get_protection(treatment_technology, override_config=None):
    config = get_input_config(override_config)
    data = core.get_protection([treatment_technology], [config.input_data.location_id])
    data = data[['location_id', 'measure', 'treatment_technology', f'draw_{config.run_configuration.input_draw_number}']]
    return data.rename(columns={f'draw_{config.run_configuration.input_draw_number}': 'protection'})


def get_healthcare_annual_visits(healthcare_entity, override_config=None):
    config = get_input_config(override_config)
    data = core.get_healthcare_annual_visit_count([healthcare_entity], [config.input_data.location_id])
    return _clean_and_filter_data(data, config.run_configuration.input_draw_number, 'annual_visits')
