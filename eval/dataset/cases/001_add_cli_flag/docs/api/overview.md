# API Overview

`mytool` exposes a small Python API for embedding the pipeline in your own programs.

## Core objects

- `Pipeline` - orchestrates a single run end to end.
- `Config` - holds resolved settings loaded from a config file or passed directly.

## Basic usage

    from mytool import Pipeline, Config

    cfg = Config.load("config.yaml")
    Pipeline(cfg).run()
