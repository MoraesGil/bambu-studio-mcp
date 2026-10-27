# SPDX-License-Identifier: AGPL-3.0-only
"""Read Bambu PrintConfig declarations as metadata; never evaluate C++ code."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from bisect import bisect_right
from collections.abc import Iterator, Mapping
from pathlib import Path

from .domain.types.json import JsonObject, JsonScalar, json_object

_QUOTED = re.compile(r'"((?:\\.|[^"\\])*)"')
_LEXEMES = re.compile(r'(?P<string>"(?:\\.|[^"\\])*")|(?P<line>//[^\n]*)|'
                      r'(?P<block>/\*.*?\*/)|(?P<unclosed>/\*.*\Z)|(?P<text>[^"/]+|.)', re.S)
_NEW_OPTION = re.compile(r'def\s*=\s*this->add(?P<nullable>_nullable)?\s*\(')
_NAMED_OPTION = re.compile(r'\s*"([^"]+)"\s*,\s*(co\w+)\s*\)')
_FIELDS = {'label': 'label', 'full_label': 'full_label', 'category': 'category',
           'tooltip': 'tooltip', 'sidetext': 'unit'}


def _strip_comments(source: str) -> str:
    pieces = []
    for token in _LEXEMES.finditer(source):
        kind = token.lastgroup
        value = token.group()
        if kind == 'unclosed':
            raise ValueError('Unterminated C++ block comment')
        pieces.append(' ' + '\n' * value.count('\n') if kind in {'line', 'block'} else value)
    return ''.join(pieces)


def _literal(source: str) -> str | None:
    strings = _QUOTED.findall(source)
    if not strings:
        return None
    escaped = ''.join(strings)
    replacements = {'n': '\n', 't': ' ', '"': '"', '\\': '\\'}
    return re.sub(r'\\([nt"\\])', lambda part: replacements[part[1]], escaped).strip()


def _statements(source: str) -> Iterator[str]:
    # Strings are lexical atoms, so semicolons in translated tooltips survive.
    pending = ''
    for match in _LEXEMES.finditer(source):
        text = match.group()
        if match.lastgroup == 'string':
            pending += text
            continue
        chunks = text.split(';')
        pending += chunks[0]
        for chunk in chunks[1:]:
            yield pending.strip()
            pending = chunk
    if pending.strip():
        yield pending.strip()


def _number(source: str) -> JsonScalar:
    text = source.strip()
    if not text:
        return None
    if re.fullmatch(r'[+-]?\d+', text):
        return int(text)
    try:
        return float(text)
    except ValueError:
        return text


def _blank(kind: str) -> JsonObject:
    result: JsonObject = dict.fromkeys(('label', 'category', 'tooltip', 'unit', 'type', 'min', 'max',
                                      'mode', 'enum_values', 'enum_labels', 'default', 'nullable'))
    result.update(type=kind, nullable=False)
    return result


def _braces(source: str, opening: int) -> tuple[str, int]:
    depth = 0
    for token in _LEXEMES.finditer(source, opening):
        if token.lastgroup == 'string':
            continue
        for offset, character in enumerate(token.group()):
            if character == '{':
                depth += 1
            elif character == '}':
                depth -= 1
                if depth == 0:
                    return source[opening + 1:token.start() + offset], token.start() + offset + 1
    raise ValueError('Unterminated native axis declaration')


def _unroll_axes(source: str) -> str:
    loop = re.search(r'for\s*\(\s*const AxisDefault\s*&\s*axis\s*:\s*axes\s*\)\s*\{', source)
    if loop is None:
        return source
    table = re.search(r'std::vector<AxisDefault>\s+axes\s*\{', source)
    if table is None:
        raise ValueError('Native axis loop has no supported literal table')
    rows, _ = _braces(source, table.end() - 1)
    template, end = _braces(source, loop.end() - 1)
    values = re.findall(r'\{\s*"([^"\\]+)"\s*,\s*\{([^{}]*)\}\s*,\s*\{([^{}]*)\}\s*,\s*\{([^{}]*)\}\s*\}', rows)
    if not values:
        raise ValueError('Cannot extract native machine axes')
    generated = []
    for name, feedrate, acceleration, jerk in values:
        body = template
        for field, numbers in (('max_feedrate', feedrate), ('max_acceleration', acceleration), ('max_jerk', jerk)):
            body = body.replace('axis.' + field, numbers)
        body = re.sub(r'"([^"\n]+)"\s*\+\s*axis\.name',
                      lambda part: json.dumps(part[1] + name), body)
        body = re.sub(r'\(boost::format\("([^"\n]+)"\)\s*%\s*axis_upper\)\.str\(\)',
                      lambda part: json.dumps(part[1].replace('%1%', name.upper())), body)
        generated.append(body)
    return source[:loop.start()] + '\n'.join(generated) + source[end:]


def _append(record: JsonObject, field: str, entry: JsonScalar) -> None:
    if entry is None:
        return
    existing = record[field]
    if existing is None:
        record[field] = [entry]
    elif isinstance(existing, list):
        existing.append(entry)
    else:
        raise ValueError('Enum entries must be a JSON array')


def _read_record(body: str, kind: str, constants: Mapping[str, JsonScalar],
                 enum_tables: Mapping[str, list[str]]) -> JsonObject:
    result = _blank(kind)
    for statement in _statements(body):
        append = re.match(r'def->enum_(values|labels)\.(?:push_back|emplace_back)\((.*)\)', statement, re.S)
        if append:
            value: JsonScalar = _literal(append[2])
            if value is None and append[1] == 'values':
                value = constants.get(append[2].strip())
            _append(result, 'enum_' + append[1], value)
            continue
        assign = re.match(r'def->(\w+)\s*=\s*(.*)', statement, re.S)
        if assign:
            field, expression = assign[1], assign[2].strip()
            copied = re.fullmatch(r'(\w+)->enum_(?:values|labels)', expression)
            if field in {'enum_values', 'enum_labels'} and copied:
                result['_copy_' + field] = copied[1]
            elif field in _FIELDS:
                translated = _literal(expression)
                if translated is not None:
                    result[_FIELDS[field]] = translated
            elif field in {'min', 'max'}:
                result[field] = constants.get(expression, _number(expression))
            elif field == 'mode':
                result[field] = expression or None
            elif field == 'nullable':
                result[field] = expression == 'true'
            elif field == 'enum_keys_map':
                enum_type = re.search(r'ConfigOptionEnum<(\w+)>', expression)
                if enum_type and enum_type[1] in enum_tables:
                    result['_enum_table'] = list(enum_tables[enum_type[1]])
            continue
        default = re.search(r'set_default_value\(\s*new\s+\w+(?:<[^;]+?>)?\s*[({](.*)[)}]\s*\)\s*$', statement, re.S)
        if default:
            result['default'] = default[1].strip() or None
    return result


def parse_print_config(text: str, constants: Mapping[str, JsonScalar] | None = None,
                       project_schema: tuple[str, int] | None = None) -> tuple[dict[str, JsonObject], list[str]]:
    native = _unroll_axes(_strip_comments(text).partition('CLIActionsConfigDef::')[0])
    if project_schema is not None:
        native = native.replace('this->add(project_schema.config_key,',
                                'this->add(' + json.dumps(project_schema[0]) + ',')
        native = native.replace('project_schema.current_version', str(project_schema[1]))
    symbols: dict[str, JsonScalar] = dict(constants or {})
    for symbol, number in re.findall(r'\bconst\s+(?:int|float|double)\s+(\w+)\s*=\s*([-+0-9.eE]+)\s*;', native):
        symbols[symbol] = _number(number)
    enum_tables = {symbol: re.findall(r'\{\s*"([^"\\]+)"\s*,', entries)
                   for symbol, entries in re.findall(
                       r'static\s+(?:const\s+)?t_config_enum_values\s+s_keys_map_(\w+)\s*(?:=\s*)?\{(.*?)\};', native, re.S)}
    declarations = list(_NEW_OPTION.finditer(native))
    positions = [item.start() for item in declarations]
    settings: dict[str, JsonObject] = {}
    for declaration in declarations:
        name = _NAMED_OPTION.match(native, declaration.end())
        if declaration['nullable'] or name is None:
            continue
        boundary = bisect_right(positions, declaration.start())
        end = positions[boundary] if boundary < len(positions) else len(native)
        settings.setdefault(name[1], _read_record(native[name.end():end], name[2], symbols, enum_tables))
    aliases = dict(re.findall(r'auto\s+(\w+)\s*=\s*def\s*=\s*this->add\(\s*"([^"]+)"', native))
    for record in settings.values():
        table = record.pop('_enum_table', None)
        if table:
            record['enum_values'] = table
        for field in ('enum_values', 'enum_labels'):
            alias = record.pop('_copy_' + field, None)
            if isinstance(alias, str) and alias in aliases:
                origin = settings[aliases[alias]][field]
                if isinstance(origin, list):
                    record[field] = list(origin)
    for loop in re.finditer(r'for\s*\(\s*const char\s*\*\s*opt_key\s*:\s*\{(.*?)\}\s*\)\s*\{'
                            r'[^{}]*?this->add_nullable\(\s*std::string\("([^"]+)"\)\s*\+\s*opt_key', native, re.S):
        for key in _QUOTED.findall(loop[1]):
            if key not in settings:
                raise ValueError(f'Missing native base setting for nullable {key}')
            settings[loop[2] + key] = {**settings[key], 'nullable': True}
    arrays = dict(re.findall(r'const std::vector<std::string>\s+(\w+_override_keys)\s*=\s*\{(.*?)\};', native, re.S))
    for array, entries in arrays.items():
        if not re.search(r'for\s*\(\s*auto\s*&\s*opt_key\s*:\s*' + re.escape(array) + r'\s*\)', native):
            continue
        for key in _QUOTED.findall(entries):
            base = key.removeprefix('filament_')
            if base not in settings:
                raise ValueError(f'Missing native base setting for nullable {key}')
            settings[key] = {**settings[base], 'nullable': True}
    missing = [key for key, record in settings.items() if not record['label'] and not record['tooltip']]
    return settings, missing


def generate_schema(source: Path, source_commit: str) -> JsonObject:
    if re.fullmatch('[0-9a-f]{40}', source_commit) is None:
        raise ValueError('Specify the full native engine source commit')
    selected = source.resolve()
    root = selected.parents[2]
    paths = (selected, selected.with_suffix('.hpp'), selected.with_name('Config.hpp'),
             selected.with_name('PresetBundle.cpp'), root / 'version.inc')
    dependencies: JsonObject = {}
    inputs: dict[str, str] = {}
    for path in paths:
        relative = path.relative_to(root).as_posix()
        contents = path.read_bytes()
        actual = hashlib.sha1(b'blob ' + str(len(contents)).encode() + b'\0' + contents).hexdigest()
        reference = subprocess.check_output(['git', '-C', str(root), 'rev-parse',
                                              source_commit + ':' + relative], text=True).strip()
        if actual != reference:
            raise ValueError(f'Source does not match declared native commit: {relative}')
        dependencies[relative] = dict(sha256=hashlib.sha256(contents).hexdigest(), git_blob=actual)
        inputs[path.name] = contents.decode('utf-8-sig')
    native_version = re.search(r'set\(SLIC3R_VERSION\s+"([^"\\]+)"\)', inputs['version.inc'])
    if native_version is None:
        raise ValueError('Cannot extract native Bambu Studio version')
    constants = dict(re.findall(r'constexpr\s+const char\s*\*\s*(\w+)\s*=\s*"([^"\\]+)"', inputs['PrintConfig.hpp']))
    settings, missing = parse_print_config(inputs[selected.name], constants)
    cleaned = _strip_comments(inputs[selected.name])
    def native_set(symbol: str) -> list[str]:
        match = re.search(r'std::set<std::string>\s+' + symbol + r'\s*=\s*\{(.*?)\};', cleaned, re.S)
        if match is None:
            raise ValueError(f'Cannot extract native {symbol} keys')
        return _QUOTED.findall(match[1])
    axes: JsonObject = {}
    for domain, suffix in (('filament', 'filament_options_with_variant'), ('process', 'print_options_with_variant'),
                           ('printer', 'printer_options_with_variant_1'), ('printer_silent', 'printer_options_with_variant_2')):
        entries = native_set(suffix)
        axes[domain] = [entry for entry in entries]
        for key in entries:
            if key not in settings:
                raise ValueError(f'Unknown native {domain} variant setting: {key}')
            settings[key].update(variant_domain='printer' if domain == 'printer_silent' else domain,
                                 variant_stride=2 if domain == 'printer_silent' else 1)
    if len(settings) < 600:
        raise ValueError('Native schema unexpectedly contains fewer than 600 settings')
    metadata: JsonObject = dict(repository='bambulab/BambuStudio', source='src/libslic3r/PrintConfig.cpp',
        source_commit=source_commit, source_sha256=json_object(dependencies['src/libslic3r/PrintConfig.cpp'])['sha256'],
        source_dependencies=dependencies, native_version=native_version[1], setting_count=len(settings),
        unparsed_keys=[key for key in sorted(missing)], variant_keys=axes,
        extruder_keys=[key for key in native_set('printer_extruder_options')],
        scope='PrintConfigDef source metadata; default expressions are not runtime values',
        extractor_origin='bambu-studio-mcp/schema_extract.py')
    return {'_meta': metadata, **{key: settings[key] for key in sorted(settings)}}


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--src', type=Path, required=True)
    cli.add_argument('--out', type=Path, required=True)
    cli.add_argument('--source-commit', required=True)
    options = cli.parse_args()
    document = generate_schema(options.src, options.source_commit)
    options.out.parent.mkdir(parents=True, exist_ok=True)
    options.out.write_text(json.dumps(document, ensure_ascii=False, indent=1, allow_nan=False) + '\n', encoding='utf-8')
    print(f"Extracted {json_object(document['_meta'])['setting_count']} native settings at {options.source_commit}")


if __name__ == '__main__':
    main()
