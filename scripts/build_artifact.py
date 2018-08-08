import logging
import argparse

from vivarium.framework.configuration import build_model_specification
from vivarium.framework.plugins import PluginManager
from vivarium.interface.interactive import InteractiveContext


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('model_specification', type=str)
    parser.add_argument('--output_path', type=str)
    parser.add_argument('--from_scratch', '-s', action="store_true",
                        help="Do not reuse any data in the artifact, if any exists")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    model_specification = build_model_specification(args.simulation_configuration)
    model_specification.plugins.optional.update({
        "data": {
            "controller": "vivarium_inputs.data_artifact.ArtifactBuilder",
            "builder_interface": "vivarium_public_health.dataset_manager.ArtifactManagerInterface",
        }})

    output_path = get_output_path(args.output_path, model_specification.configuration)

    plugin_config = model_specification.plugins
    component_config = model_specification.components
    simulation_config = model_specification.configuration

    plugin_manager = PluginManager(plugin_config)
    component_config_parser = plugin_manager.get_plugin('component_configuration_parser')
    components = component_config_parser.get_components(component_config)

    simulation = InteractiveContext(simulation_config, components, plugin_manager)
    simulation.data.start_processing(simulation.component_manager, output_path,
                                     [simulation.configuration.input_data.location],
                                     incremental=not args.from_scratch)
    simulation.setup()
    simulation.data.end_processing()


def get_output_path(command_line_arg, configuration):
    if command_line_arg:
        return command_line_arg
    elif 'artifact' in configuration and 'path' in configuration.artifact and configuration.artifact.path is not None:
        return configuration.artifact.path
    else:
        raise argparse.ArgumentError(
            "specify --output_path or include configuration.artifact.path in model specification")


if __name__ == "__main__":
    main()
