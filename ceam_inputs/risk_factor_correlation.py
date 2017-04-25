import os, re

import pandas as pd

from ceam import config
from ceam.interpolation import Interpolation

from ceam_inputs.auxiliary_files import auxiliary_file_path
from ceam_inputs.gbd_mapping import risk_factors

COLUMN_NORMALIZATION = {
        'sbp': risk_factors.systolic_blood_pressure.name,
        'bmi': risk_factors.body_mass_index.name,
        'chol': risk_factors.cholesterol.name,
        'smoke2': risk_factors.smoking.name,
        'fpg2': risk_factors.fasting_plasma_glucose.name,
}

def load_matrices():
    matrices_root = auxiliary_file_path('Risk Factor Propensity Correlation Matrices', matrix_variation=config.input_data.risk_factor_correlation_matrix_variation)
    knot_ages, sexes = zip(*[re.match('corr_([0-9]+)_([A-Za-z]+).csv', path).groups() for path in os.listdir(matrices_root)])
    knot_ages = set(knot_ages)
    sexes = set(sexes)

    matrices = pd.DataFrame()
    for age in knot_ages:
        for sex in sexes:
            df = pd.read_csv(
                    os.path.join(matrices_root, 'corr_{}_{}.csv'.format(age, sex))
                )
            columns = list(df.columns)
            columns[0] = 'risk_factor'
            df.columns = columns
            df['age'] = age
            df['sex'] = sex
            matrices = matrices.append(df)
    matrices = matrices.rename(columns=COLUMN_NORMALIZATION)
    matrices['age'] = matrices.age.astype(int)
    matrices['risk_factor'] = matrices.applymap(COLUMN_NORMALIZATION.get)

    return matrices
