# External real traces

Ten 100-minute request traces (requests per minute) built by
`dataset/tools/ita_to_traces.py` from two public web-server logs of the Internet Traffic
Archive (https://ita.ee.lbl.gov/html/traces.html), whose pages state that the traces may be
freely redistributed:

- `NASA_access_log_Jul95.gz`: NASA Kennedy Space Center WWW server, July 1995.
- `clarknet_access_log_Aug28.gz`: ClarkNet, 28 August - 3 September 1995.

The windows are fixed by calendar rule in the script. The raw logs are not stored here;
download them into `dataset/external_raw/ita/` and run the script to rebuild.
