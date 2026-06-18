# CLI Reference

The `mytool` command-line interface accepts the following options.

## Options

- `--config PATH` - Path to the configuration file. Defaults to `./config.yaml`.
- `--output DIR` - Directory for generated output. Created if it does not exist.
- `--dry-run` - Simulate the run without writing any files.

## Examples

Run with an explicit config:

    mytool --config ./prod.yaml --output ./build

Preview changes without writing anything:

    mytool --dry-run
