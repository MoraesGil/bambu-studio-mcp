"""Compatibility codec for the published MCP envelopes on FastMCP 4."""
from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from typing import ParamSpec, cast

from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError, ResourceError, ToolError
from fastmcp.resources import ResourceResult
from fastmcp.resources.template import FunctionResourceTemplate
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import Tool, ToolResult
from mcp import types
from mcp.shared.exceptions import MCPError
from pydantic import Field, TypeAdapter, ValidationError
from pydantic.json_schema import SkipJsonSchema
from pydantic_core import InitErrorDetails
from typing_extensions import TypeForm

from ...domain.types.json import JsonObject, JsonValue, decode_json, json_object

ToolValue = JsonObject | None
P = ParamSpec('P')
R = ParamSpec('R')


def _content(value: ToolValue) -> list[types.ContentBlock]:
    if value is None:
        return []
    serialized = json.dumps(value, ensure_ascii=False, indent=2)
    return [types.TextContent(type='text', text=serialized)]


class ContractTool(Tool):
    invoke: SkipJsonSchema[Callable[[JsonObject], Awaitable[ToolValue]]] = Field(exclude=True)

    def to_mcp_tool(self, **overrides: JsonValue) -> types.Tool:
        declaration = {'name': self.name, 'description': self.description,
                       'input_schema': self.parameters}
        return types.Tool.model_validate(declaration)

    async def run(self, arguments: JsonObject) -> ToolResult:
        try:
            result = await self.invoke(arguments)
        except Exception as failure:
            message = f'Error executing tool {self.name}: {failure}'
            raise ToolError(message) from failure
        return ToolResult(content=_content(result))


class ContractTemplate(FunctionResourceTemplate):
    def to_mcp_template(self, **overrides: JsonValue) -> types.ResourceTemplate:
        declaration = dict(name=self.name, uri_template=self.uri_template,
                           description=self.description, mime_type=self.mime_type)
        return types.ResourceTemplate.model_validate(declaration)


class LegacyErrors(Middleware):
    async def on_call_tool(self, context: MiddlewareContext[types.CallToolRequestParams],
                           call_next: CallNext[types.CallToolRequestParams, ToolResult]) -> ToolResult:
        try:
            result = await call_next(context)
        except NotFoundError:
            contents = types.TextContent(type='text', text='Unknown tool: ' + context.message.name)
            return ToolResult(content=[contents], is_error=True)
        return result

    async def on_read_resource(self, context: MiddlewareContext[types.ReadResourceRequestParams],
                               call_next: CallNext[types.ReadResourceRequestParams, ResourceResult]) -> ResourceResult:
        try:
            result = await call_next(context)
        except NotFoundError as failure:
            message = 'Unknown resource: ' + str(context.message.uri)
            raise MCPError(0, message) from failure
        except ResourceError as failure:
            leaf: BaseException = failure
            while isinstance(leaf, ResourceError):
                if leaf.__cause__ is None:
                    break
                leaf = leaf.__cause__
            envelope = 'Error creating resource from template: '
            raise MCPError(0, envelope * 2 + str(leaf)) from failure
        return result


def _arguments(arguments: JsonObject, fields: JsonObject) -> JsonObject:
    accepted = {key: value for key, value in arguments.items() if key in fields}
    for key in accepted:
        supplied = accepted[key]
        if isinstance(supplied, str) and json_object(fields[key]).get('type') != 'string':
            try:
                parsed = decode_json(supplied)
            except ValueError:
                continue
            if parsed is None or isinstance(parsed, (list, dict, bool)):
                accepted[key] = parsed
    return accepted


def _validation_error(error: ValidationError, title: str) -> ValidationError:
    entries: list[InitErrorDetails] = []
    for failure in error.errors(include_url=False):
        detail: InitErrorDetails = dict(type=failure['type'], loc=failure['loc'], input=failure.get('input'))
        if failure['type'] == 'missing_argument':
            detail['type'] = 'missing'
        if 'ctx' in failure:
            detail['ctx'] = failure['ctx']
        entries.append(detail)
    return ValidationError.from_exception_data(title, entries)


def register_tool(server: FastMCP[None]) -> Callable[[Callable[P, ToolValue | Awaitable[ToolValue]]], Callable[P, ToolValue | Awaitable[ToolValue]]]:
    def install(declaration: Callable[P, ToolValue | Awaitable[ToolValue]]) -> Callable[P, ToolValue | Awaitable[ToolValue]]:
        adapter = TypeAdapter(cast(TypeForm[ToolValue | Awaitable[ToolValue]], declaration))
        specification = json_object(adapter.json_schema())
        title = declaration.__name__ + 'Arguments'
        specification.update(title=title)
        specification.pop('additionalProperties', None)
        fields = json_object(specification['properties'])

        async def execute(arguments: JsonObject) -> ToolValue:
            try:
                result = adapter.validate_python(_arguments(arguments, fields))
            except ValidationError as failure:
                raise _validation_error(failure, title) from failure
            return await result if inspect.isawaitable(result) else result

        documentation = declaration.__doc__ or ''
        description = inspect.cleandoc(documentation)
        if '\n' in documentation:
            description += '\n'
        tool = ContractTool(name=declaration.__name__, description=description,
                            parameters=specification, invoke=execute)
        server.add_tool(tool)
        return declaration
    return install


def register_resource(server: FastMCP[None], uri: str) -> Callable[[Callable[R, str]], Callable[R, str]]:
    def install(declaration: Callable[R, str]) -> Callable[R, str]:
        resource = ContractTemplate.from_function(declaration, uri_template=uri, description='')
        server.add_template(resource)
        return declaration
    return install
