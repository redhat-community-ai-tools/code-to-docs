# Usage Guide

This guide covers everyday use of `mytool`.

## Running a job

Point `mytool` at a config file and it runs the pipeline end to end:

    mytool --config config.yaml

## Where output goes

By default, results are written to the `build/` directory. Override this with the
`output_dir` setting in your config file.
