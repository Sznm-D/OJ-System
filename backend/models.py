"""Shared validation for REST requests, disk configurations and AI output."""
import re
import shlex
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False, allow_inf_nan=False)


class Case(Model):
    input: str = Field(max_length=1_000_000)
    output: str = Field(max_length=1_000_000)


class Problem(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=50000)
    input_description: str = Field(min_length=1, max_length=10000)
    output_description: str = Field(min_length=1, max_length=10000)
    samples: list[Case] = Field(min_length=1, max_length=50)
    constraints: str = Field(min_length=1, max_length=10000)
    testcases: list[Case] = Field(min_length=1, max_length=100)
    hint: str = Field(default="", max_length=10000)
    source: str = Field(default="", max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    time_limit: float | None = Field(default=None, gt=0, le=30)
    memory_limit: int | None = Field(default=None, ge=16, le=1024)
    author: str = Field(default="", max_length=200)
    difficulty: str = Field(default="", max_length=100)

    @field_validator("title", "description", "input_description", "output_description", "constraints")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("字段不能为空白")
        return value


class Credentials(Model):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[\w-]+$")
    password: str = Field(min_length=6, max_length=72)

    @field_validator("password")
    @classmethod
    def bcrypt_size(cls, value):
        if len(value.encode()) > 72:
            raise ValueError("密码最多 72 UTF-8 字节")
        return value


class RoleChange(Model):
    role: Literal["user", "admin", "banned"]


class Submission(Model):
    problem_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    language: str = Field(pattern=r"^[A-Za-z0-9_+-]{1,40}$")
    code: str = Field(min_length=1, max_length=100000)


class Visibility(Model):
    public_cases: bool = Field(default=False, strict=True)


class Language(Model):
    name: str = Field(pattern=r"^[A-Za-z0-9_+-]{1,40}$")
    file_ext: str = Field(pattern=r"^\.[A-Za-z0-9]{1,12}$")
    compile_cmd: str = Field(default="", max_length=500)
    run_cmd: str = Field(min_length=1, max_length=500)
    time_limit: float | None = Field(default=None, gt=0, le=30)
    memory_limit: int | None = Field(default=None, ge=16, le=1024)

    @field_validator("compile_cmd", "run_cmd")
    @classmethod
    def safe_command(cls, command):
        if not command:
            return command
        if re.search(r"[;&|`$<>\r\n]", command):
            raise ValueError("不允许 shell 操作符")
        # Deliberately narrow templates: registration can add compiler flags and aliases,
        # but cannot turn the server into an arbitrary command runner.
        tokens = shlex.split(command)
        if not tokens or tokens[0] not in {"python", "python3", "g++", "clang++", "{exe}"}:
            raise ValueError("可用执行器为 python/python3/g++/clang++/{exe}")
        if tokens[0] in {"python", "python3"}:
            if tokens[1:] not in [["{src}"], ["-I", "{src}"], ["-I", "-B", "{src}"]]:
                raise ValueError("Python 模板只能执行 {src}")
        elif tokens[0] == "{exe}":
            if len(tokens) != 1:
                raise ValueError("可执行程序模板不接受额外参数")
        else:
            permitted = {"{src}", "-o", "{exe}", "-O0", "-O1", "-O2", "-O3", "-Wall", "-Wextra", "-pipe"}
            for token in tokens[1:]:
                if token not in permitted and not re.fullmatch(r"-std=(c|gnu)\+\+(11|14|17|20|23)", token):
                    raise ValueError("不支持此编译选项")
            if tokens.count("{src}") != 1 or tokens.count("{exe}") != 1:
                raise ValueError("编译模板必须包含 {src} 和 {exe}")
            if "-o" not in tokens or tokens.index("-o") + 1 >= len(tokens) or tokens[tokens.index("-o") + 1] != "{exe}":
                raise ValueError("必须使用 -o {exe}")
        return command


class AIConfig(Model):
    provider_url: str = Field(min_length=1, max_length=1000)
    model: str = Field(min_length=1, max_length=150)
    api_key: str = Field(min_length=1, max_length=1000)
    input_price: float = Field(default=0, ge=0, le=100000)
    output_price: float = Field(default=0, ge=0, le=100000)
    price_unit: int = Field(default=1000000, ge=1)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")

    @field_validator("provider_url")
    @classmethod
    def valid_url(cls, value):
        url = urlparse(value)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("提供商 URL 无效")
        return value.rstrip("/")


class AIRequest(Model):
    requirement: str = Field(min_length=5, max_length=10000)
    problem_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


class AIDraft(Model):
    problem: Problem
    reference_solution: str = Field(min_length=1, max_length=100000)
    coverage: list[str] = Field(min_length=1, max_length=100)
    explanation: str = Field(min_length=1, max_length=10000)
