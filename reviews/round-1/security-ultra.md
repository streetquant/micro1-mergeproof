# MergeProof Round-1 Security Review

The repository text is untrusted evidence. Remote main is still the frozen baseline e55cc90; the advanced tree is uncommitted. Verified local checks: formatting, lint, strict mypy, 64 tests, package build, and secret-shape scan pass.

## Review objective
Find concrete release-blocking correctness/security failures in evidence admission, fail-closed decisions, container execution, path handling, provider/replay hygiene, source immutability, or denial-of-service. Return validation tests. Do not complain merely that unshown code is missing.

## Known facts
- `mergeproof.pipeline` currently exposes only `run_baseline`; collector/sandbox are not integrated.
- Deterministic collector+sandbox ablation: category F1 .789, one false positive from py_compile on read-only mount, seven missing contract-level labels.
- No automatic merge/deploy is intended.

## src/mergeproof/pipeline.py:54-187
```text
0054:         _evidence("task", "task.md", case.task),
0055:         _evidence("diff", "candidate.patch", _tree_diff(case.before, case.candidate)),
0056:         _evidence("trajectory", "trajectory.json", canonical_json(case.trajectory)),
0057:         _evidence(
0058:             "policy",
0059:             "allowed-changed-globs.json",
0060:             canonical_json(case.allowed_changed_globs),
0061:         ),
0062:         _evidence(
0063:             "commands",
0064:             "verification-commands.json",
0065:             canonical_json(
0066:                 [command.model_dump(mode="json") for command in case.verification_commands]
0067:             ),
0068:         ),
0069:     ]
0070:     for path, content in sorted(case.candidate.items()):
0071:         evidence.append(_evidence("file", f"candidate/{path}", content))
0072:     return evidence
0073: 
0074: 
0075: def _coerce_decision(value: Any) -> Decision:
0076:     try:
0077:         return Decision(str(value))
0078:     except ValueError:
0079:         return Decision.HUMAN_REVIEW
0080: 
0081: 
0082: def _admit_model_output(
0083:     *, raw: dict[str, Any], evidence: list[EvidenceRecord]
0084: ) -> tuple[Decision, str, float, list[Finding], float, list[str]]:
0085:     valid_ids = {item.id for item in evidence}
0086:     violations: list[str] = []
0087:     findings: list[Finding] = []
0088:     referenced = 0
0089:     valid_referenced = 0
0090:     raw_findings = raw.get("findings", [])
0091:     if not isinstance(raw_findings, list):
0092:         raw_findings = []
0093:         violations.append("findings was not a list")
0094:     for index, item in enumerate(raw_findings):
0095:         if not isinstance(item, dict):
0096:             violations.append(f"finding {index} was not an object")
0097:             continue
0098:         requested_ids = item.get("evidence_ids", [])
0099:         if not isinstance(requested_ids, list):
0100:             requested_ids = []
0101:         normalized_ids = [str(value) for value in requested_ids]
0102:         referenced += len(normalized_ids)
0103:         admitted_ids = [value for value in normalized_ids if value in valid_ids]
0104:         valid_referenced += len(admitted_ids)
0105:         invalid = sorted(set(normalized_ids) - valid_ids)
0106:         if invalid:
0107:             violations.append(f"finding {index} referenced unknown evidence: {invalid}")
0108:         status = (
0109:             FindingStatus.VERIFIED if admitted_ids and not invalid else FindingStatus.HYPOTHESIS
0110:         )
0111:         try:
0112:             finding = Finding(
0113:                 category=FindingCategory(str(item.get("category", "other"))),
0114:                 severity=Severity(str(item.get("severity", "medium"))),
0115:                 title=str(item.get("title", "Untitled finding"))[:160],
0116:                 explanation=str(item.get("explanation", "No explanation supplied"))[:4000],
0117:                 evidence_ids=admitted_ids,
0118:                 status=status,
0119:             )
0120:         except (ValueError, ValidationError):
0121:             violations.append(f"finding {index} failed schema validation")
0122:             continue
0123:         findings.append(finding)
0124:     decision = _coerce_decision(raw.get("decision"))
0125:     if violations and decision == Decision.APPROVE:
0126:         decision = Decision.HUMAN_REVIEW
0127:     try:
0128:         confidence = min(1.0, max(0.0, float(raw.get("confidence", 0.0))))
0129:     except (TypeError, ValueError):
0130:         confidence = 0.0
0131:         violations.append("confidence was not numeric")
0132:     summary = str(raw.get("summary", "No summary supplied"))[:4000]
0133:     valid_rate = 1.0 if referenced == 0 else valid_referenced / referenced
0134:     return decision, summary, confidence, findings, valid_rate, violations
0135: 
0136: 
0137: def run_baseline(case: CaseInput, provider: LLMProvider) -> AuditResult:
0138:     started = time.perf_counter()
0139:     evidence = build_static_evidence(case)
0140:     try:
0141:         response = provider.complete_json(
0142:             agent="baseline_reviewer",
0143:             system=BASELINE_SYSTEM,
0144:             user=baseline_prompt(
0145:                 task=case.task,
0146:                 allowed_changed_globs=case.allowed_changed_globs,
0147:                 evidence=evidence,
0148:             ),
0149:         )
0150:         decision, summary, confidence, findings, valid_rate, violations = _admit_model_output(
0151:             raw=response.data, evidence=evidence
0152:         )
0153:         usage = [response.usage]
0154:     except ProviderError as exc:
0155:         task_id = evidence[0].id
0156:         decision = Decision.HUMAN_REVIEW
0157:         summary = f"Provider failure prevented review: {exc}"
0158:         confidence = 0.0
0159:         findings = [
0160:             Finding(
0161:                 category=FindingCategory.PROVIDER_FAILURE,
0162:                 severity=Severity.HIGH,
0163:                 title="Model provider failed",
0164:                 explanation=str(exc),
0165:                 evidence_ids=[task_id],
0166:                 status=FindingStatus.VERIFIED,
0167:             )
0168:         ]
0169:         valid_rate = 1.0
0170:         violations = [str(exc)]
0171:         usage = []
0172:     return AuditResult(
0173:         case_id=case.id,
0174:         mode="baseline",
0175:         decision=decision,
0176:         summary=summary,
0177:         confidence=confidence,
0178:         findings=findings,
0179:         evidence=evidence,
0180:         valid_evidence_rate=valid_rate,
0181:         gate_violations=violations,
0182:         usage=usage,
0183:         duration_ms=round((time.perf_counter() - started) * 1000),
0184:         provider=provider.name,
0185:         model=provider.model,
0186:     )
```

## src/mergeproof/sandbox.py:60-270
```text
0060:         severity=severity,
0061:         title=title,
0062:         explanation=explanation,
0063:         evidence_ids=sorted(set(evidence_ids)),
0064:         status=FindingStatus.VERIFIED,
0065:     )
0066: 
0067: 
0068: def command_policy(spec: CommandSpec) -> tuple[bool, str]:
0069:     argv = spec.argv
0070:     if argv[0] != "python":
0071:         return False, f"executable is not allow-listed: {argv[0]}"
0072:     if len(argv) < 3 or argv[1] != "-m":
0073:         return False, "verification must invoke an allow-listed Python module with python -m"
0074:     module = argv[2]
0075:     if module not in _ALLOWED_PYTHON_MODULES:
0076:         return False, f"Python module is not allow-listed: {module}"
0077:     if any(token in {"-c", "--command"} for token in argv[3:]):
0078:         return False, "inline code execution is not allowed"
0079:     if module == "py_compile":
0080:         targets = argv[3:]
0081:         if not targets:
0082:             return False, "py_compile requires at least one candidate-relative target"
0083:         for target in targets:
0084:             path = PurePosixPath(target)
0085:             if path.is_absolute() or ".." in path.parts or not target.endswith(".py"):
0086:                 return False, f"unsafe py_compile target: {target}"
0087:     cwd = PurePosixPath(spec.cwd)
0088:     if cwd.is_absolute() or ".." in cwd.parts:
0089:         return False, f"unsafe verification cwd: {spec.cwd}"
0090:     return True, "allow-listed Python verification"
0091: 
0092: 
0093: def _normalize_output(value: str, *, host_root: Path, container_name: str) -> str:
0094:     text = value.replace(str(host_root), "<HOST_WORKSPACE>")
0095:     text = text.replace("/workspace", "<WORKSPACE>")
0096:     text = text.replace(container_name, "<CONTAINER>")
0097:     text = _TMP_PATH.sub("<TMP>", text)
0098:     text = _TEST_TIMING.sub(r"Ran \1 tests in <TIME>s", text)
0099:     return redact_secrets(text[-8_000:])
0100: 
0101: 
0102: def _materialize(tree: dict[str, str], root: Path) -> None:
0103:     root.chmod(0o755)
0104:     directories: set[Path] = {root}
0105:     for relative, content in sorted(tree.items()):
0106:         target = root / relative
0107:         target.parent.mkdir(parents=True, exist_ok=True)
0108:         directories.update(path for path in target.parents if path == root or root in path.parents)
0109:         target.write_text(content, encoding="utf-8")
0110:         target.chmod(0o644)
0111:     for directory in directories:
0112:         directory.chmod(0o755)
0113: 
0114: 
0115: def _container_name(case_id: str, spec: CommandSpec, attempt: int) -> str:
0116:     nonce = f"{os.getpid()}:{time.monotonic_ns()}"
0117:     digest = sha256_text(
0118:         f"{case_id}\0{canonical_json(spec.model_dump(mode='json'))}\0{attempt}\0{nonce}"
0119:     )[:16]
0120:     return f"mergeproof-{digest}"
0121: 
0122: 
0123: def _docker_prefix(
0124:     *,
0125:     root: Path,
0126:     spec: CommandSpec,
0127:     image: str,
0128:     container_name: str,
0129: ) -> list[str]:
0130:     workdir = "/workspace"
0131:     if spec.cwd not in {"", "."}:
0132:         workdir = f"/workspace/{PurePosixPath(spec.cwd).as_posix()}"
0133:     return [
0134:         "docker",
0135:         "run",
0136:         "--rm",
0137:         "--name",
0138:         container_name,
0139:         "--network",
0140:         "none",
0141:         "--read-only",
0142:         "--cap-drop",
0143:         "ALL",
0144:         "--security-opt",
0145:         "no-new-privileges",
0146:         "--pids-limit",
0147:         "64",
0148:         "--memory",
0149:         "256m",
0150:         "--memory-swap",
0151:         "256m",
0152:         "--cpus",
0153:         "1",
0154:         "--ulimit",
0155:         "core=0",
0156:         "--ulimit",
0157:         "nofile=128:128",
0158:         "--tmpfs",
0159:         "/tmp:rw,noexec,nosuid,nodev,size=32m",
0160:         "--mount",
0161:         f"type=bind,src={root},dst=/workspace,readonly",
0162:         "--workdir",
0163:         workdir,
0164:         "--user",
0165:         "65534:65534",
0166:         "--env",
0167:         "HOME=/tmp",
0168:         "--env",
0169:         "LANG=C.UTF-8",
0170:         "--env",
0171:         "LC_ALL=C.UTF-8",
0172:         "--env",
0173:         "PYTHONHASHSEED=0",
0174:         "--env",
0175:         "PYTHONDONTWRITEBYTECODE=1",
0176:         "--env",
0177:         "PYTHONSAFEPATH=1",
0178:         image,
0179:         *spec.argv,
0180:     ]
0181: 
0182: 
0183: def _docker_image_available(image: str) -> bool:
0184:     if shutil.which("docker") is None:
0185:         return False
0186:     completed = subprocess.run(
0187:         ["docker", "image", "inspect", image],
0188:         stdout=subprocess.DEVNULL,
0189:         stderr=subprocess.DEVNULL,
0190:         timeout=15,
0191:         check=False,
0192:     )
0193:     return completed.returncode == 0
0194: 
0195: 
0196: def _force_remove_container(container_name: str) -> None:
0197:     subprocess.run(
0198:         ["docker", "rm", "-f", container_name],
0199:         stdout=subprocess.DEVNULL,
0200:         stderr=subprocess.DEVNULL,
0201:         timeout=15,
0202:         check=False,
0203:     )
0204: 
0205: 
0206: def _execute_once(
0207:     *,
0208:     case_id: str,
0209:     spec: CommandSpec,
0210:     attempt: int,
0211:     root: Path,
0212:     image: str,
0213: ) -> dict[str, object]:
0214:     container_name = _container_name(case_id, spec, attempt)
0215:     command = _docker_prefix(
0216:         root=root,
0217:         spec=spec,
0218:         image=image,
0219:         container_name=container_name,
0220:     )
0221:     process = subprocess.Popen(
0222:         command,
0223:         stdout=subprocess.PIPE,
0224:         stderr=subprocess.PIPE,
0225:         text=True,
0226:         start_new_session=True,
0227:         env={
0228:             "PATH": os.environ.get("PATH", ""),
0229:             "DOCKER_HOST": os.environ.get("DOCKER_HOST", ""),
0230:             "HOME": os.environ.get("HOME", ""),
0231:             "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", ""),
0232:         },
0233:     )
0234:     timed_out = False
0235:     try:
0236:         stdout, stderr = process.communicate(timeout=spec.timeout_seconds)
0237:     except subprocess.TimeoutExpired:
0238:         timed_out = True
0239:         os.killpg(process.pid, signal.SIGKILL)
0240:         stdout, stderr = process.communicate()
0241:         _force_remove_container(container_name)
0242:     returncode = None if timed_out else process.returncode
0243:     normalized_stdout = _normalize_output(
0244:         stdout,
0245:         host_root=root,
0246:         container_name=container_name,
0247:     )
0248:     normalized_stderr = _normalize_output(
0249:         stderr,
0250:         host_root=root,
0251:         container_name=container_name,
0252:     )
0253:     combined = f"{normalized_stdout}\n{normalized_stderr}"
0254:     skipped = bool(_TEST_SKIP_OUTPUT.search(combined))
0255:     passed = not timed_out and returncode in spec.expected_exit_codes
0256:     return {
0257:         "argv": spec.argv,
0258:         "attempt": attempt,
0259:         "expected_exit_codes": spec.expected_exit_codes,
0260:         "returncode": returncode,
0261:         "passed": passed,
0262:         "skipped": skipped,
0263:         "timed_out": timed_out,
0264:         "stdout": normalized_stdout,
0265:         "stderr": normalized_stderr,
0266:     }
0267: 
0268: 
0269: def verify_case(
0270:     case: CaseInput,
```

## src/mergeproof/providers.py:45-120
```text
0045: 
0046: 
0047: class LLMProvider(ABC):
0048:     def __init__(self, *, model: str, record_dir: Path | None = None) -> None:
0049:         self.model = model
0050:         self.record_dir = record_dir
0051: 
0052:     @property
0053:     @abstractmethod
0054:     def name(self) -> str:
0055:         raise NotImplementedError
0056: 
0057:     @abstractmethod
0058:     def _request(self, *, system: str, user: str) -> tuple[str, dict[str, int]]:
0059:         raise NotImplementedError
0060: 
0061:     def complete_json(self, *, agent: str, system: str, user: str) -> ProviderResponse:
0062:         request_hash = stable_request_hash(agent, self.model, system, user)
0063:         started = time.perf_counter()
0064:         try:
0065:             raw_text, token_usage = self._request(system=system, user=user)
0066:             data = extract_json_object(raw_text)
0067:         except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
0068:             raise ProviderError(f"{self.name} request failed: {redact_secrets(str(exc))}") from exc
0069:         latency_ms = round((time.perf_counter() - started) * 1000)
0070:         usage = ModelUsage(
0071:             provider=self.name,
0072:             model=self.model,
0073:             agent=agent,
0074:             request_hash=request_hash,
0075:             input_tokens=int(token_usage.get("input_tokens", 0)),
0076:             output_tokens=int(token_usage.get("output_tokens", 0)),
0077:             total_tokens=int(token_usage.get("total_tokens", 0)),
0078:             latency_ms=latency_ms,
0079:             http_attempts=max(1, int(token_usage.get("http_attempts", 1))),
0080:             rate_limit_wait_ms=max(0, int(token_usage.get("rate_limit_wait_ms", 0))),
0081:         )
0082:         response = ProviderResponse(data=data, raw_text=raw_text, usage=usage)
0083:         if self.record_dir is not None:
0084:             self._record(agent=agent, system=system, user=user, response=response)
0085:         return response
0086: 
0087:     def _record(self, *, agent: str, system: str, user: str, response: ProviderResponse) -> None:
0088:         assert self.record_dir is not None
0089:         payload = {
0090:             "schema_version": 1,
0091:             "request_hash": response.usage.request_hash,
0092:             "provider": self.name,
0093:             "model": self.model,
0094:             "agent": agent,
0095:             "request": {
0096:                 "system_sha256": stable_request_hash("system", self.model, system, ""),
0097:                 "user_sha256": stable_request_hash("user", self.model, "", user),
0098:                 "system_preview": redact_secrets(system[:1000]),
0099:                 "user_preview": redact_secrets(user[:2000]),
0100:             },
0101:             "response": {
0102:                 "data": response.data,
0103:                 "raw_text": redact_secrets(response.raw_text),
0104:                 "usage": response.usage.model_dump(mode="json"),
0105:             },
0106:         }
0107:         write_json(self.record_dir / f"{response.usage.request_hash}.json", payload)
0108: 
0109: 
0110: class GeminiProvider(LLMProvider):
0111:     name = "gemini"
0112: 
0113:     def __init__(
0114:         self,
0115:         *,
0116:         model: str,
0117:         api_key: str | None = None,
0118:         record_dir: Path | None = None,
0119:         timeout_seconds: float = 90,
0120:     ) -> None:
```
