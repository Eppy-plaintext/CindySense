"""Disposable TinyIoT discovery/FCNT/TS probe. Mutates only its own AE child.

Run explicitly; this module never contacts TinyIoT when imported. A failed health
check halts the probe, and cleanup is attempted only while the CSE is healthy.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE = os.environ.get("CINDYSENSE_TINYIOT_URL", "http://127.0.0.1:3001/TinyIoT").rstrip("/")
PARENT = "CindySense_AE"
ORIGIN = os.environ.get("CINDYSENSE_PROBE_ORIGIN", "CAdmin")


def request(method, path, body=None, ty=None, query=None):
    headers = {
        "X-M2M-Origin": ORIGIN, "X-M2M-RVI": "4",
        "X-M2M-RI": "advanced-" + uuid.uuid4().hex,
        "Accept": "application/json",
    }
    if body is not None:
        headers["Content-Type"] = "application/json" + (f";ty={ty}" if ty else "")
    url = f"{BASE}/{path}"
    if query:
        url += "?" + urlencode(query, doseq=True)
    req = Request(url, data=json.dumps(body).encode() if body is not None else None,
                  headers=headers, method=method)
    try:
        response = urlopen(req, timeout=8)
    except HTTPError as exc:
        response = exc
    with response:
        raw = response.read()
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {"unparseable_body": True}
        return response.status, response.headers.get("X-M2M-RSC"), data


def checked_health():
    status, rsc, _ = request("GET", PARENT)
    if (status, rsc) != (200, "2000"):
        raise RuntimeError(f"TinyIoT health changed: HTTP {status}, RSC {rsc}")


def uri_names(data):
    refs = data.get("m2m:uril", [])
    if not isinstance(refs, list):
        raise RuntimeError(f"Unexpected discovery representation: {list(data)}")
    return {str(ref).rstrip("/").rsplit("/", 1)[-1] for ref in refs}


def probe_group(record, parent, root, member_ids):
    """Probe a disposable sibling GRP; CNT cannot have a GRP child on TinyIoT."""
    group_name = root.rsplit('/', 1)[-1] + '_GRP'
    group_path = f'{parent}/{group_name}'
    member_paths = [f'TinyIoT/{root}/labeled', f'TinyIoT/{root}/plain']
    entry, _ = record('GRP POST', 'POST', parent,
                      body={'m2m:grp': {'rn': group_name, 'mt': 3, 'mid': member_paths,
                                        'mnm': 2, 'csy': 2}}, ty=9)
    entry['passed'] = (entry['http'], entry['rsc']) == (201, '2001')
    if not entry['passed']:
        entry['reason'] = 'GRP create failed; readback and fan-out skipped'
        return None
    readback_entry, stored = record('GRP GET', 'GET', group_path)
    grp = stored.get('m2m:grp') if isinstance(stored, dict) else None
    grp = grp if isinstance(grp, dict) else {}
    stored_members = grp.get('mid')
    # The CSE normalizes local member URIs to resource IDs in stored mid.
    readback_entry['member_ids'] = grp.get('mid')
    readback_entry['passed'] = ((readback_entry['http'], readback_entry['rsc']) == (200, '2000') and
                                grp.get('mt') == 3 and grp.get('csy') == 2 and
                                grp.get('mnm') == 2 and isinstance(stored_members, list) and
                                set(stored_members) == set(member_ids))
    if not readback_entry['passed']:
        readback_entry['reason'] = 'GRP representation or member IDs mismatch'
        return group_path
    fanout_entry, aggregate = record('GRP fopt GET', 'GET', group_path + '/fopt')
    agr = aggregate.get('m2m:agr') if isinstance(aggregate, dict) else None
    responses = agr.get('m2m:rsp', []) if isinstance(agr, dict) else []
    if isinstance(responses, dict):
        responses = [responses]
    if not isinstance(responses, list):
        responses = []
    fanout_entry['member_rsc'] = [response.get('rsc') for response in responses if isinstance(response, dict)]
    fanout_entry['member_types'] = [list(response.get('pc') or {}) for response in responses if isinstance(response, dict)]
    fanout_entry['passed'] = ((fanout_entry['http'], fanout_entry['rsc']) == (200, '2000') and
                              len(responses) == 2 and all(isinstance(response, dict) and
                              str(response.get('rsc')) == '2000' and
                              isinstance(response.get('pc'), dict) and
                              'm2m:cnt' in response['pc'] for response in responses))
    if not fanout_entry['passed']:
        fanout_entry['reason'] = 'Fan-out member responses did not match two successful CNT retrievals'
    return group_path


def main():
    name = "AdvancedProbe_" + uuid.uuid4().hex[:12]
    root = f"{PARENT}/{name}"
    records = []
    created = False
    group_path = None
    healthy = True

    def record(label, method, path, *, body=None, ty=None, query=None, expected=None):
        nonlocal healthy
        try:
            status, rsc, data = request(method, path, body, ty, query)
        except (URLError, TimeoutError, OSError) as exc:
            healthy = False
            raise RuntimeError(f"TinyIoT unavailable during {label}: {type(exc).__name__}") from exc
        entry = {"case": label, "http": status, "rsc": rsc}
        records.append(entry)
        if expected and (status, rsc) not in expected:
            checked_health()  # abort if server crashed, even on an HTTP error
            raise RuntimeError(f"Unexpected {label}: HTTP {status}, RSC {rsc}")
        if method != "GET":
            try:
                checked_health()
            except (URLError, TimeoutError, OSError, RuntimeError):
                healthy = False
                raise
        return entry, data

    try:
        checked_health()
        entry, _ = record("root POST", "POST", PARENT,
                          body={"m2m:cnt": {"rn": name}}, ty=3)
        created = (entry["http"], entry["rsc"]) == (201, "2001")
        if not created:
            raise RuntimeError(f"Root create failed: HTTP {entry['http']}, RSC {entry['rsc']}")
        record("root GET", "GET", root, expected={(200, "2000")})
        marker = "advanced-" + uuid.uuid4().hex[:10]
        record("labeled CNT POST", "POST", root,
               body={"m2m:cnt": {"rn": "labeled", "lbl": [marker]}}, ty=3,
               expected={(201, "2001")})
        record("plain CNT POST", "POST", root,
               body={"m2m:cnt": {"rn": "plain"}}, ty=3,
               expected={(201, "2001")})
        record("CIN POST", "POST", root,
               body={"m2m:cin": {"rn": "sample", "con": "probe"}}, ty=4,
               expected={(201, "2001")})
        _, labeled = record("labeled CNT GET", "GET", root + "/labeled",
                            expected={(200, "2000")})
        if marker not in labeled["m2m:cnt"].get("lbl", []):
            raise RuntimeError("Labeled CNT readback lost marker")
        _, plain = record("plain CNT GET", "GET", root + "/plain",
                          expected={(200, "2000")})
        member_ids = [labeled['m2m:cnt']['ri'], plain['m2m:cnt']['ri']]

        filters = [
            ("label match", {"lbl": marker}, {"labeled"}),
            ("label miss", {"lbl": "no-such-" + marker}, set()),
            ("type CNT", {"ty": 3}, {"labeled", "plain"}),
            ("type CIN", {"ty": 4}, {"sample"}),
            ("created after old cutoff", {"cra": "20000101T000000"},
             {"labeled", "plain", "sample"}),
            ("created before old cutoff", {"crb": "20000101T000000"}, set()),
            ("created after future cutoff", {"cra": "20990101T000000"}, set()),
            ("created before future cutoff", {"crb": "20990101T000000"},
             {"labeled", "plain", "sample"}),
        ]
        for label, filter_query, expected_names in filters:
            entry, data = record(label, "GET", root,
                                 query={"fu": 1, "rcn": 11, **filter_query},
                                 expected={(200, "2000")})
            names = uri_names(data)
            entry["names"] = sorted(names)
            entry["passed"] = names == expected_names

        group_path = probe_group(record, PARENT, root, member_ids)
        # Modification filters use an explicit PUT, so there is no clock-edge guess.
        record("labeled CNT PUT", "PUT", root + "/labeled",
               body={"m2m:cnt": {"lbl": [marker, "updated"]}},
               expected={(200, "2004")})
        for label, query, expected_names in [
            ("modified after old cutoff", {"ms": "20000101T000000"},
             {"labeled", "plain", "sample"}),
            ("modified after future cutoff", {"ms": "20990101T000000"}, set()),
            ("unmodified since old cutoff", {"us": "20000101T000000"}, set()),
            ("unmodified since future cutoff", {"us": "20990101T000000"},
             {"labeled", "plain", "sample"}),
        ]:
            entry, data = record(label, "GET", root,
                                 query={"fu": 1, "rcn": 11, **query},
                                 expected={(200, "2000")})
            names = uri_names(data)
            entry["names"] = sorted(names)
            entry["passed"] = names == expected_names

        # The installed source requires an SDT-registered CND. Use its generic
        # interworking schema solely to verify FCNT operations, not workout data.
        fcnt = root + "/schema_probe"
        entry, _ = record("FCNT POST", "POST", root,
                          body={"m2m:fcnt": {"rn": "schema_probe",
                                "cnd": "org.onem2m.genericInterworkingService",
                                "gisn": "disposable"}}, ty=28,
                          expected={(201, "2001")})
        if (entry["http"], entry["rsc"]) == (201, "2001"):
            _, fc_data = record("FCNT GET", "GET", fcnt, expected={(200, "2000")})
            if not any(value.get("gisn") == "disposable" for value in fc_data.values()
                       if isinstance(value, dict)):
                raise RuntimeError("FCNT custom attribute missing on readback")
            entry, data = record("type FCNT", "GET", root,
                                 query={"fu": 1, "rcn": 11, "ty": 28},
                                 expected={(200, "2000")})
            entry["names"] = sorted(uri_names(data))
            entry["passed"] = set(entry["names"]) == {"schema_probe"}
            record("FCNT PUT", "PUT", fcnt,
                   body={"m2m:fcnt": {"lbl": [marker]}},
                   expected={(200, "2004")})
            record("FCNT DELETE", "DELETE", fcnt, expected={(200, "2002")})
            record("FCNT absent GET", "GET", fcnt, expected={(404, "4004")})
        elif entry["http"] >= 500:
            raise RuntimeError("FCNT server error; stopping")

        ts = root + "/series_probe"
        entry, _ = record("TS POST", "POST", root,
                          body={"m2m:ts": {"rn": "series_probe", "mni": 3}}, ty=29,
                          expected={(201, "2001")})
        if (entry["http"], entry["rsc"]) == (201, "2001"):
            record("TS GET", "GET", ts, expected={(200, "2000")})
            entry, data = record("type TS", "GET", root,
                                 query={"fu": 1, "rcn": 11, "ty": 29},
                                 expected={(200, "2000")})
            entry["names"] = sorted(uri_names(data))
            entry["passed"] = set(entry["names"]) == {"series_probe"}
            record("TS PUT", "PUT", ts,
                   body={"m2m:ts": {"lbl": [marker]}},
                   expected={(200, "2004")})
            dgt = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            tsi = ts + "/point_probe"
            record("TSI POST", "POST", ts,
                   body={"m2m:tsi": {"rn": "point_probe", "dgt": dgt,
                         "con": "1"}}, ty=30, expected={(201, "2001")})
            _, ts_data = record("TS with point GET", "GET", ts,
                                expected={(200, "2000")})
            if ts_data["m2m:ts"].get("cni") != 1:
                raise RuntimeError("TS cni did not increase after TSI create")
            record("TSI GET", "GET", tsi, expected={(200, "2000")})
            record("TSI DELETE", "DELETE", tsi, expected={(200, "2002")})
            _, ts_data = record("TS after point DELETE GET", "GET", ts,
                                expected={(200, "2000")})
            if ts_data["m2m:ts"].get("cni") != 0:
                raise RuntimeError("TS cni did not decrease after TSI delete")
            record("TS DELETE", "DELETE", ts, expected={(200, "2002")})
            record("TS absent GET", "GET", ts, expected={(404, "4004")})
        elif entry["http"] >= 500:
            raise RuntimeError("TS server error; stopping")

        if not all(row.get("passed", True) for row in records
                   if not row['case'].startswith('GRP')):
            raise RuntimeError("Discovery result mismatch; stopping")
    finally:
        if group_path and healthy:
            try:
                checked_health()
                record("GRP DELETE", "DELETE", group_path, expected={(200, "2002")})
                record("GRP absent GET", "GET", group_path, expected={(404, "4004")})
            except Exception as exc:
                records.append({"case": "GRP cleanup error", "error": str(exc)})
        if created and healthy:
            try:
                checked_health()
                record("root DELETE", "DELETE", root, expected={(200, "2002")})
                record("root absent GET", "GET", root, expected={(404, "4004")})
            except Exception as exc:
                records.append({"case": "cleanup error", "error": str(exc)})
        print(json.dumps({"test_root": root, "records": records,
                          "cleanup_attempted": created and healthy}, indent=2))


if __name__ == "__main__":
    main()
