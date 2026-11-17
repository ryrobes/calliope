"""Install the Calliope memory scope (calliope_memory_scope) for Hindsight.

/v1/runs accepts ``calliope_memory`` and binds it for the run. Memory providers
then read the person's own words instead of the whole Calliope turn, and new
Hindsight memories carry the conversation's audience. With
CALLIOPE_MEMORY_SCOPE=audience, recall and reflect only return memories
everyone present could see; the default keeps memory shared. Runs on the
hermes-run-context patch (bind_run_id)."""
import os
from pathlib import Path

root = Path(os.environ.get('HERMES_SOURCE_ROOT', '/opt/hermes'))


def patch(file, old, new):
    path = root / file
    source = path.read_text()
    if source.count(old) != 1:
        raise SystemExit('Calliope memory scope seam changed: ' + file + ': ' + old[:80])
    path.write_text(source.replace(old, new))


RUNS = 'gateway/platforms/api_server_runs.py'
patch(RUNS,
      'from gateway.platforms.api_server_run_idempotency import TERMINAL_STATUSES\n',
      'from gateway.platforms.api_server_run_idempotency import TERMINAL_STATUSES\n'
      'from gateway import calliope_memory_scope as _calliope_memory_scope\n')
patch(RUNS,
      '    turn_author: Optional[Dict[str, Any]] = None  # memory-attribution label only; grants nothing\n',
      '    turn_author: Optional[Dict[str, Any]] = None  # memory-attribution label only; grants nothing\n'
      '    calliope_memory: Optional[Dict[str, Any]] = None  # Calliope: memory text + audience; grants nothing\n')
patch(RUNS,
      '        turn_author=turn_author)\n'
      '    self._activate_admitted_request()\n',
      '        turn_author=turn_author,\n'
      '        calliope_memory=_calliope_memory_scope.parse(body.get("calliope_memory")))\n'
      '    self._activate_admitted_request()\n')
patch(RUNS,
      '    from gateway.calliope_run_context import bind_run_id\n',
      '    from gateway.calliope_run_context import bind_run_id\n'
      '    from gateway.calliope_memory_scope import bind as bind_memory_scope\n')
patch(RUNS,
      '    with self._profile_scope(run.request_profile), bind_run_id(run.run_id):\n',
      '    with self._profile_scope(run.request_profile), bind_run_id(run.run_id), \\\n'
      '            bind_memory_scope(run.calliope_memory):\n')

patch('agent/memory_manager.py',
      '    _strip_skill_scaffolding = staticmethod(extract_user_instruction_from_skill_message)\n',
      '    @staticmethod\n'
      '    def _strip_skill_scaffolding(message):\n'
      '        # Calliope: a /v1/runs turn names the person\'s own words for memory;\n'
      '        # the message itself also carries Calliope\'s per-turn context.\n'
      '        from gateway.calliope_memory_scope import memory_text\n'
      '        return memory_text(message, extract_user_instruction_from_skill_message)\n')

HINDSIGHT = 'plugins/memory/hindsight/__init__.py'
patch(HINDSIGHT,
      '        if self._recall_types:\n'
      '            kwargs["types"] = self._recall_types\n'
      '        resp = self._run_hindsight_operation(lambda client: client.arecall(**kwargs))\n'
      '        return resp.results or []\n',
      '        if self._recall_types:\n'
      '            kwargs["types"] = self._recall_types\n'
      '        # Calliope: with CALLIOPE_MEMORY_SCOPE=audience, only what everyone here could see.\n'
      '        kwargs["tag_groups"] = _calliope_memory_scope.recall_tag_groups(_calliope_memory_scope.provider_scope(self))\n'
      '        return _calliope_memory_scope.recall(self, kwargs)\n')
patch(HINDSIGHT,
      '        resp = self._run_hindsight_operation(\n'
      '            lambda client: client.areflect(bank_id=self._bank_id, query=query, budget=self._budget)\n'
      '        )\n'
      '        return resp.text\n',
      '        # Calliope: reflect over the same memories recall may show.\n'
      '        return _calliope_memory_scope.reflect(self, {\n'
      '            "query": query, "budget": self._budget,\n'
      '            "tag_groups": _calliope_memory_scope.recall_tag_groups(_calliope_memory_scope.provider_scope(self))})\n')
patch(HINDSIGHT,
      '        merged_tags = _normalize_retain_tags(list(self._retain_tags) + _normalize_retain_tags(tags))\n',
      '        merged_tags = _normalize_retain_tags(list(self._retain_tags) + _normalize_retain_tags(tags)\n'
      '                                             + _calliope_memory_scope.retain_tags(_calliope_memory_scope.provider_scope(self)))\n')
patch(HINDSIGHT,
      '        tags = [f"{kind}:{sid}" for kind, sid in lineage if sid] or None\n',
      '        tags = ([f"{kind}:{sid}" for kind, sid in lineage if sid]\n'
      '                + _calliope_memory_scope.retain_tags(_calliope_memory_scope.provider_scope(self))) or None\n')
patch(HINDSIGHT,
      'def register(ctx) -> None:\n'
      '    """Register Hindsight as a memory provider plugin."""\n',
      'from gateway import calliope_memory_scope as _calliope_memory_scope  # noqa: E402  (Calliope)\n'
      '\n\n'
      'def register(ctx) -> None:\n'
      '    """Register Hindsight as a memory provider plugin."""\n')
patch(HINDSIGHT,
      '        item.update({k: v for k, v in (("tags", merged_tags), ("observation_scopes", self._observation_scopes)) if v})\n'
      '        return item\n',
      '        item.update({k: v for k, v in (("tags", merged_tags), ("observation_scopes", self._observation_scopes)) if v})\n'
      '        # Calliope: merge observations across chats (per audience when scoped), not per chat.\n'
      '        _calliope_scopes = _calliope_memory_scope.observation_scopes(_calliope_memory_scope.provider_scope(self))\n'
      '        if _calliope_scopes is not None:\n'
      '            item["observation_scopes"] = _calliope_scopes\n'
      '        return item\n')
patch(HINDSIGHT,
      '            logger.debug("Hindsight %s succeeded", label)\n',
      '            try:  # Calliope: the bank\'s retain mission (the provider reads but never sends it)\n'
      '                _calliope_memory_scope.ensure_retain_mission(self)\n'
      '            except Exception as exc:\n'
      '                logger.debug("Hindsight retain mission not applied: %s", exc)\n'
      '            logger.debug("Hindsight %s succeeded", label)\n')
patch('agent/memory_manager.py',
      '        clean_user_content = self._strip_skill_scaffolding(user_content) if providers else None\n'
      '        if not clean_user_content:\n'
      '            return\n',
      '        clean_user_content = self._strip_skill_scaffolding(user_content) if providers else None\n'
      '        if not clean_user_content:\n'
      '            return\n'
      '        from gateway.calliope_memory_scope import retains\n'
      '        if not retains():  # Calliope: its own extractor writes this turn\'s memories\n'
      '            return\n')
