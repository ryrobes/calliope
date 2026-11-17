"""Connector-owned form contracts. No credentials or generated HTML in model context.

The renderer only knows field types, groups and conditions. Connector adapters own
their requirements and how a submitted secret envelope becomes an executable
connection. MCP adapters reuse the canonical action/catalog field definitions.
"""
import base64
import copy
import json
import re
from urllib.parse import quote, urlencode

PORTS = {"postgresql": 5432, "mysql": 3306, "mariadb": 3306, "mssql": 1433, "oracle": 1521, "db2": 50000}
NAMES = {"postgresql": "PostgreSQL", "mysql": "MySQL", "mariadb": "MariaDB", "mssql": "Microsoft SQL Server", "oracle": "Oracle", "db2": "IBM Db2", "bigquery": "Google BigQuery"}


def field(key, label, kind="text", **options):
    return {"key": key, "label": label, "type": kind, **options}


def choices(mapping):
    return [{"value": key, "label": label} for key, label in mapping.items()]


def visible(spec, values):
    condition = spec.get("when") or {}
    return all(values.get(key) in (expected if isinstance(expected, list) else [expected])
               for key, expected in condition.items())


def sql_contract(dialect, *, defaults=None):
    """One SQL recipe composed with transport/authentication recipes."""
    network = {"entry_mode": "fields", "dialect": list(PORTS)}
    bigquery = {"entry_mode": "fields", "dialect": "bigquery"}
    ssh = {"transport": "ssh", "dialect": list(PORTS)}
    fields = [
        field("dialect", "Database type", "select", options=choices(NAMES), default=dialect, required=True),
        field("entry_mode", "Connection details", "select", options=choices({"fields": "Enter the details I have", "url": "Use a connection URL"}), default="fields"),
        field("host", "Server address", required=True, placeholder="db.company.com or an IP address", when=network),
        field("database", "Database / service name", required=True, when=network),
        field("username", "Username", required=True, when=network, autocomplete="username"),
        field("password", "Password", "secret", when=network, help="Use an account with read-only access to the source."),
        field("project", "Google Cloud project", required=True, when=bigquery),
        field("service_account", "Service account JSON", "secret", multiline=True, required=True, when=bigquery),
        field("source_dsn", "Connection URL", "secret", required=True, when={"entry_mode": "url"}, help="The whole URL is treated as a secret, including any password or options."),
        field("port", "Database port", group="advanced", placeholder="Use the database default", when=network),
        field("driver_options", "Additional driver options", "secret", multiline=True, group="advanced", when=network, help='Optional JSON object, for example {"sslmode":"require"}. Values stay in the encrypted connection.'),
        field("transport", "How to reach this database", "select", options=choices({"direct": "Direct connection", "ssh": "Through an SSH server"}), default="direct", when={"dialect": list(PORTS)}),
        field("ssh_host", "SSH server address", required=True, when=ssh, group="ssh"),
        field("ssh_port", "SSH port", default="22", when=ssh, group="ssh"),
        field("ssh_username", "SSH username", required=True, when=ssh, group="ssh"),
        field("ssh_auth", "SSH sign-in", "select", options=choices({"key": "Private key", "password": "Password"}), default="key", when=ssh, group="ssh"),
        field("ssh_private_key", "SSH private key", "secret", multiline=True, required=True, when={**ssh, "ssh_auth": "key"}, group="ssh"),
        field("ssh_password", "SSH password", "secret", required=True, when={**ssh, "ssh_auth": "password"}, group="ssh"),
        field("ssh_passphrase", "Private key passphrase", "secret", when={**ssh, "ssh_auth": "key"}, group="ssh"),
        field("ssh_host_key", "Expected SSH host key", "textarea", when=ssh, group="advanced", help="Optional known_hosts line from your administrator. Otherwise the worker remembers the first key it sees and rejects changes."),
        field("tls_ca", "Trusted CA certificate (PEM)", "secret", multiline=True, group="advanced", when={"dialect": ["postgresql", "mysql", "mariadb"]}),
        field("tls_cert", "Client certificate (PEM)", "secret", multiline=True, group="advanced", when={"dialect": ["postgresql", "mysql", "mariadb"]}),
        field("tls_key", "Client private key (PEM)", "secret", multiline=True, group="advanced", when={"dialect": ["postgresql", "mysql", "mariadb"]}),
    ]
    safe_defaults = {k: v for k, v in (defaults or {}).items()
                     if any(f["key"] == k and f["type"] != "secret" for f in fields)}
    return {"version": 1, "adapter": "sql", "envelope_key": "secret:CONNECTION_INPUTS", "title": "Connect your database", "fields": fields,
            "values": {"dialect": dialect, **safe_defaults},
            "groups": {"ssh": "SSH connection", "advanced": "More connection options"},
            "description": "Enter the details you have. Your password goes straight to the encrypted store; Callie only sees the connection result.",
            "explanation": "Your questions run on a local copy. Only the initial copy and scheduled refreshes read this source. You will choose what to copy next."}


def contract(action, values=None, missing_names=None):
    values = values or {}
    if action.get("executor") == "mirror_connect":
        return sql_contract(values.get("dialect") or "postgresql")
    if action.get('executor') in {'sms_connect', 'sms_user'}:
        fields = copy.deepcopy(action['fields'])
        for item in fields:
            if item['type'] == 'text':
                item['required'] = True
        connect = action['executor'] == 'sms_connect'
        return {'version': 1, 'adapter': 'action', 'title': action['title'], 'fields': fields,
                'description': ('Your Twilio Auth Token is encrypted. Callie sees only the connection result.' if connect else
                                'Confirm this phone belongs to this person. Texts will run with their Calliope access and replies will be sent automatically.'),
                'explanation': ('Twilio needs a publicly reachable HTTPS webhook. Connecting sends no text; only explicitly linked phone numbers can start conversations.' if connect else
                                'Use an existing login. To transfer a number, first revoke its current mapping, then link the new user; their conversation starts fresh.'),
                'values': {f['key']: values.get(f['key'], f.get('default', '')) for f in fields if f['type'] != 'secret'},
                'groups': {}, 'submit_label': 'Test and connect' if connect else 'Save phone access',
                'pending_label': 'Connecting…' if connect else 'Saving…',
                'error_label': 'Couldn’t connect yet' if connect else 'Couldn’t save yet',
                'footer_note': 'Encrypted entry · Callie continues after the test' if connect else 'Callie continues after you save'}
    if action.get('executor') == 'mail_connect':
        fields = copy.deepcopy(action['fields'])
        fields[0]['required'] = True
        return {'version': 1, 'adapter': 'action', 'title': 'Connect your mailbox',
                'description': 'Enter the app password for this mailbox. Callie receives only the connection result and continues here after the test.',
                'explanation': 'Gmail / Google Workspace defaults are ready. For another provider, change the server details below. Incoming collection starts when connected; you review outgoing drafts before sending.',
                'fields': fields, 'values': {f['key']: values.get(f['key'], f.get('default', '')) for f in fields if f['type'] != 'secret'},
                'groups': {'advanced': 'Mail server details'}, 'submit_label': 'Test and connect'}
    fields = []
    secret_groups = []
    missing = set(missing_names or [])
    for raw in action.get("fields") or []:
        item = copy.deepcopy(raw)
        if item.get("type") not in {"text", "textarea", "secret", "select", "boolean"}:
            continue
        key = item["key"]
        if item.get("secret_group") and missing:
            members = {}
            for name in sorted(missing):
                entry_key = "secret:entry:" + name
                members[entry_key] = name
                fields.append(field(entry_key, name.replace('_', ' ').title(), "secret", required=True))
            secret_groups.append({"key": key, "members": members})
            continue
        if item.get("type") == "secret":
            name = item.get("secret_name") or key.removeprefix("secret:")
            item["required"] = bool(item.get("required")) or name in missing
            item.pop("default", None)
        elif key in values:
            item["default"] = values[key]
        if item.get("type") != "secret" and key in values:
            item.setdefault("group", "advanced")
        condition = item.pop("visible_when", None)
        if condition and condition.get("field"):
            item["when"] = {condition["field"]: condition.get("values", [condition.get("value")])}
        fields.append(item)
    return {"version": 1, "adapter": "action", "title": action.get("title") or "Connect a service",
            "description": "Check the connection details and enter any requested credentials here. Secrets never go into the conversation.",
            "fields": fields, "secret_groups": secret_groups, "groups": {"advanced": "Connection details"},
            "values": {k: v for k, v in values.items() if not k.startswith("secret:")}}


def normalize(contract, raw):
    if not isinstance(raw, dict):
        raise ValueError("Enter the connection details in the secure form.")
    fields = contract["fields"]
    if set(raw) - {f["key"] for f in fields}:
        raise ValueError("The connection form changed. Refresh it before submitting again.")
    values = {f["key"]: raw.get(f["key"], f.get("default", "")) for f in fields}
    clean = {}
    for spec in fields:
        if not visible(spec, values):
            continue
        key = spec["key"]
        value = values[key]
        if spec["type"] == "boolean":
            value = value is True or value == "true"
        else:
            if not isinstance(value, (str, int, float)):
                raise ValueError(f"Check {spec['label']}.")
            value = str(value)
            if spec["type"] != "secret":
                value = value.strip()
            if len(value) > 64000:
                raise ValueError(f"{spec['label']} is too long.")
            if spec.get("required") and not value:
                raise ValueError(f"Enter {spec['label']}.")
            if value and spec["type"] == "select" and value not in {o["value"] for o in spec.get("options", [])}:
                raise ValueError(f"Choose {spec['label']} from the list.")
        clean[key] = value
    return clean


def _port(value, default):
    try:
        result = int(value or default)
        if 0 < result < 65536:
            return result
    except (ValueError, TypeError):
        pass
    raise ValueError("Ports must be numbers between 1 and 65535.")


def sql_profile(raw, dialect):
    """Assemble a private profile; callers must never persist it as ordinary input."""
    values = normalize(sql_contract(dialect), raw)
    dialect = values["dialect"]
    if values.get("entry_mode") == "url":
        dsn = values["source_dsn"].strip()
    elif dialect == "bigquery":
        try:
            account = json.loads(values["service_account"])
            if account.get("type") != "service_account" or not account.get("private_key") or not account.get("client_email"):
                raise ValueError()
        except (ValueError, AttributeError):
            raise ValueError("Enter a Google service account JSON key.") from None
        encoded = base64.b64encode(json.dumps(account).encode()).decode()
        dsn = 'bigquery://' + quote(values['project'], safe='') + '?' + urlencode({'credentials_base64': encoded})
    else:
        host = values["host"]
        if re.search(r"[\s/@?#]", host) or host.startswith('-'):
            raise ValueError("Enter a server name or IP address, without a URL or port.")
        if ':' in host and not host.startswith('['):
            host = '[' + host + ']'
        port = _port(values.get('port'), PORTS[dialect])
        authority = f"{quote(values['username'], safe='')}:{quote(values.get('password', ''), safe='')}@{host}:{port}"
        dsn = f"{dialect}://{authority}"
        options = {}
        if values.get('driver_options'):
            try:
                options = json.loads(values['driver_options'])
                if not isinstance(options, dict) or any(not isinstance(v, (str, int, float, bool)) for v in options.values()):
                    raise ValueError()
            except ValueError:
                raise ValueError("Driver options must be a JSON object with simple named values.") from None
        if dialect == 'oracle':
            dsn += '/'
            options['service_name'] = values['database']
        else:
            dsn += '/' + quote(values['database'], safe='')
        if options:
            dsn += '?' + urlencode(options)
    profile = {'version': 1, 'dsn': dsn}
    if values.get('transport') == 'ssh':
        profile['ssh'] = {k.removeprefix('ssh_'): v for k, v in values.items() if k.startswith('ssh_') and v}
        profile['ssh']['port'] = _port(values.get('ssh_port'), 22)
    tls = {k.removeprefix('tls_'): v for k, v in values.items() if k.startswith('tls_') and v}
    if tls:
        if bool(tls.get('cert')) != bool(tls.get('key')):
            raise ValueError("A client certificate needs its matching private key.")
        profile['tls'] = tls
    return dialect, profile


def private_values(raw):
    """Values used only to scrub exception messages, never for a public result."""
    if isinstance(raw, dict):
        public = {'dialect', 'entry_mode', 'host', 'database', 'username', 'project', 'port', 'transport', 'ssh_host', 'ssh_port', 'ssh_username', 'ssh_auth', 'ssh_host_key'}
        return [v for key, value in raw.items() if key not in public for v in private_values(value)]
    if isinstance(raw, str):
        return [raw] if raw else []
    return []
