"""Tools for automatically generating the GBD mapping template"""
from .util import TAB, SPACING, make_record
from .data import get_cause_list, get_etiology_list, get_sequela_list, get_risk_list


gbd_rec_attrs = ()
me_attrs = (('name', 'str'),
            ('gbd_id', 'Union[rid, cid, sid, hid, None]'),)
hs_attrs = (('name', 'str'),
            ('gbd_id', 'hid'),)
eti_attrs = (('name', 'str'),
             ('gbd_id', 'Union[rid, None]'),)
seq_attrs = (('name', 'str'),
             ('gbd_id', 'sid'),
             ('dismod_id', 'meid'),
             ('healthstate', 'Healthstate'),)
cause_attrs = (('name', 'str'),
               ('gbd_id', 'cid'),
               ('sequelae', 'Tuple[Sequela, ...] = None'),
               ('etiologies', 'Tuple[Etiology, ...] = None'),)
restrictions_attrs = (('male_only', 'bool'),
                      ('female_only', 'bool'),
                      ('yll_only', 'bool'),
                      ('yld_only', 'bool'))
tmred_attrs = (('distribution', 'str'),
               ('min', 'scalar'),
               ('max', 'scalar'),
               ('inverted', 'bool'),)
levels_attrs = (('cat1', 'str'),
                ('cat2', 'str'),
                ('cat3', 'str = None'),
                ('cat4', 'str = None'),
                ('cat5', 'str = None'),
                ('cat6', 'str = None'),
                ('cat7', 'str = None'),
                ('cat8', 'str = None'),
                ('cat9', 'str = None'),
                ('cat10', 'str = None'),
                ('cat11', 'str = None'),
                ('cat12', 'str = None'),)
exp_params_attrs = (('scale', 'scalar = None'),
                    ('max_rr', 'scalar = None'),
                    ('max_val', 'scalar = None'),
                    ('min_val', 'scalar = None'),)
risk_attrs = (('name', 'str'),
              ('gbd_id', 'rid'),
              ('distribution', 'str'),
              ('affected_causes', 'Tuple[Cause, ...]'),
              ('restrictions', 'Restrictions'),
              ('levels', 'Levels = None'),
              ('tmred', 'Tmred = None'),
              ('exposure_parameters', 'ExposureParameters = None'),)
causes_attrs = tuple([(name, 'Cause') for name in get_cause_list()])
etiologies_attrs = tuple([(name, 'Etiology') for name in get_etiology_list()])
sequelae_attrs = tuple([(name, 'Sequela') for name in get_sequela_list()])
risks_attrs = tuple([(name, 'Risk') for name in get_risk_list()])

gbd_types = {'GbdRecord': {'attrs': gbd_rec_attrs, 'superclass': (None, ()),
                           'docstring': 'Base class for entities modeled in the GBD.'},
             'ModelableEntity': {'attrs': me_attrs, 'superclass': ('GbdRecord', gbd_rec_attrs),
                                 'docstring': 'Container for general GBD ids and metadata.'},
             'Healthstate': {'attrs': hs_attrs, 'superclass': ('ModelableEntity', me_attrs),
                             'docstring': 'Container for healthstate GBD ids and metadata.'},
             'Etiology': {'attrs': eti_attrs, 'superclass': ('ModelableEntity', me_attrs),
                          'docstring': 'Container for etiology GBD ids and metadata.'},
             'Sequela': {'attrs': seq_attrs, 'superclass': ('ModelableEntity', me_attrs),
                         'docstring': 'Container for sequela GBD ids and metadata.'},
             'Cause': {'attrs': cause_attrs, 'superclass': ('ModelableEntity', me_attrs),
                       'docstring': 'Container for cause GBD ids and metadata.'},
             'Restrictions': {'attrs': restrictions_attrs, 'superclass': ('GbdRecord', gbd_rec_attrs),
                              'docstring': 'Container for risk restriction data.'},
             'Tmred': {'attrs': tmred_attrs, 'superclass': ('GbdRecord', gbd_rec_attrs),
                       'docstring': 'Container for theoretical minimum risk exposure distribution data.'},
             'Levels': {'attrs': levels_attrs, 'superclass': ('GbdRecord', gbd_rec_attrs),
                        'docstring': 'Container for categorical risk exposure levels.'},
             'ExposureParameters': {'attrs': exp_params_attrs, 'superclass': ('GbdRecord', gbd_rec_attrs),
                                    'docstring': 'Container for continuous risk exposure distribution parameters'},
             'Risk': {'attrs': risk_attrs, 'superclass': ('GbdRecord', gbd_rec_attrs),
                      'docstring': 'Container for risk factor GBD ids and metadata.'},
             'Causes': {'attrs': causes_attrs, 'superclass': ('GbdRecord', gbd_rec_attrs),
                        'docstring': 'Container for GBD causes.'},
             'Etiologies': {'attrs': etiologies_attrs, 'superclass': ('GbdRecord', gbd_rec_attrs),
                            'docstring': 'Container for GBD etiologies.'},
             'Sequelae': {'attrs': sequelae_attrs, 'superclass': ('GbdRecord', gbd_rec_attrs),
                          'docstring': 'Container for GBD sequelae.'},
             'Risks': {'attrs': risks_attrs, 'superclass': ('GbdRecord', gbd_rec_attrs),
                       'docstring': 'Container for GBD risks.'}, }


def make_module_docstring():
    out = f'"""This code is automatically generated by /ceam_inputs/gbd_mapping/scripts/template_builder.py\n\n'
    out += 'Any manual changes will be lost.\n"""\n'
    return out


def make_imports():
    return 'from typing import Union, Tuple\n'


def make_ids():
    out = ''
    id_docstring_map = (('meid', 'Modelable Entity ID'),
                        ('rid', 'Risk Factor ID'),
                        ('cid', 'Cause ID'),
                        ('sid', 'Sequela ID'),
                        ('hid', 'Health State ID'),
                        ('scalar', 'Raw Measure Value'))
    for k, v in id_docstring_map:
        out += f'class {k}(int):\n'
        out += TAB + f'"""{v}"""\n'
        out += TAB + 'def __repr__(self):\n'
        out += 2*TAB + f'return "{k}({{:d}}).format(self)"\n'
        out += SPACING

    return out


def make_unknown_flag():
    out = ''
    out += 'class _Unknown:\n'
    out += TAB + '"""Marker for unknown values."""\n'
    out += TAB + 'def __repr__(self):\n'
    out += 2*TAB + 'return "UNKNOWN"\n' + SPACING
    out += 'UNKNOWN = _Unknown()\n' + SPACING
    out += 'class UnknownEntityError(Exception):\n'
    out += TAB + '"""Exception raised when a quantity is requested from ceam_inputs with an `UNKNOWN` id."""\n'
    out += TAB + 'pass\n'
    return out


def make_gbd_record():
    out = ''
    out += 'class GbdRecord:\n'
    out += TAB + '"""Base class for entities modeled in the GBD."""\n'
    out += TAB + '__slots__ = ()\n\n'
    out += TAB + 'def __contains__(self, item):\n'
    out += 2*TAB + 'return item in self.__slots__\n\n'
    out += TAB + 'def __getitem__(self, item):\n'
    out += 2*TAB + 'if item in self:\n'
    out += 3*TAB + 'return getattr(self, item)\n'
    out += 2*TAB + 'else:\n'
    out += 3*TAB + 'raise KeyError\n\n'
    out += TAB + 'def __iter__(self):\n'
    out += 2*TAB + 'for item in self.__slots__:\n'
    out += 3*TAB + 'yield getattr(self, item)\n\n'
    out += TAB + 'def __repr__(self):\n'
    out += 2*TAB + 'return "{}({})".format(self.__class__.__name__,\n'
    indent = len('return "{}({})".format(') + 8  # + two tabs
    out += ' '*indent + r'",\n".join(["{{}}={{}}".format(name, self[name])' + '\n'
    indent += len(r'",\n".join(')
    out += ' '*indent + 'for name in self.__slots__]))\n'
    return out


def build_templates():
    templates = ''
    templates += make_module_docstring()
    templates += make_imports() + SPACING
    templates += make_ids()
    templates += make_unknown_flag() + SPACING
    templates += make_gbd_record() + SPACING
    for entity, info in gbd_types.items():
        if entity == 'GbdRecord':
            continue
        templates += make_record(entity, **info) + SPACING

    return templates
