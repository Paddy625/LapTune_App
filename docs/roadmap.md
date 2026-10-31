# Next iterations

1. Obtain GitHub write/API access, publish the feature branch and run Windows CI. Download and
   manually exercise the frozen ZIP on Windows 10 and 11; test online Road tiles and a licensed
   satellite provider. Check a clean PC without Python, high-DPI scaling and signed distribution.
2. Extend logging with per-source timestamps/new-fix flags and explicit validity, units and
   sensor-axis metadata. Support independent GPS position, speed, IMU and CAN acquisition rates
   with a second importer and UI format selection. Report observed change rates separately
   from authoritative source measurement rates.
3. Add selected-time-range CSV/GPX export, persisted session sidecars, configurable jump/gap
   thresholds and stronger uncertainty handling through GNSS outages. Add a user-facing
   interpolation policy selector only with tested source-specific semantics.
4. Profile genuinely large logs (100k–1M rows), then consider chunked import, cached multi-scale
   min/max rendering and a spatial index for dense route clicks. No million-sample benchmark
   or corresponding performance guarantee exists yet.
5. Add separate offline CAN/OBD and DBC decoding adapters. Live vehicle connections are outside
   this MVP. Consider MoTeC export only after documenting format/licensing and implementing
   interoperability tests; there is no guessed `.ld` writer or misleading export button.
