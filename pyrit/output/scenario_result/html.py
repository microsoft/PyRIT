# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Any

from pyrit.output.base import PrinterBase

# Text-only report template (media pieces render as a placeholder + reference, not inline).
# Autoescaping is enabled at render time, so transcript values (untrusted) are HTML-escaped.
_REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>PyRIT Scenario Report — {{ report.overview.scenario.name }}</title>
<style>
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 2rem; color: #1a1a1a; }
  h1 { margin-bottom: 0.25rem; }
  .muted { color: #666; font-size: 0.9rem; }
  .cards { display: flex; flex-wrap: wrap; gap: 1rem; margin: 1rem 0; }
  .card { border: 1px solid #ddd; border-radius: 8px; padding: 0.75rem 1rem; min-width: 160px; }
  .card .label { color: #666; font-size: 0.75rem; text-transform: uppercase; }
  .card .value { font-size: 1.4rem; font-weight: 600; }
  table { border-collapse: collapse; margin: 0.5rem 0; }
  th, td { border: 1px solid #e0e0e0; padding: 0.4rem 0.6rem; text-align: left; font-size: 0.9rem; }
  th { background: #f5f5f5; }
  details.attack { border: 1px solid #ddd; border-radius: 8px; margin: 0.5rem 0; padding: 0.4rem 0.9rem; }
  details.attack > summary { cursor: pointer; font-weight: 600; }
  .outcome-success { color: #b00020; }
  .outcome-failure { color: #1b7f3b; }
  .outcome-undetermined, .outcome-error { color: #b06f00; }
  .msg { margin: 0.5rem 0; padding: 0.5rem 0.75rem; border-radius: 6px; background: #fafafa; border: 1px solid #eee; }
  .msg .role { font-weight: 600; font-size: 0.75rem; text-transform: uppercase; color: #444; }
  .piece { white-space: pre-wrap; margin: 0.25rem 0; }
  .media { color: #666; font-style: italic; }
  .reasoning { color: #555; border-left: 3px solid #ccc; padding-left: 0.5rem; }
  .score { font-size: 0.85rem; color: #444; margin-top: 0.25rem; }
  .err { color: #b00020; }
  .partial { color: #7a5c00; font-style: italic; }
</style>
</head>
<body>
  <h1>{{ report.overview.scenario.name }}</h1>
  <div class="muted">Result {{ report.scenario_result_id }} · v{{ report.overview.scenario.version }} ·
    PyRIT {{ report.overview.scenario.pyrit_version }}</div>
  {% if report.overview.scenario.description %}<p>{{ report.overview.scenario.description }}</p>{% endif %}

  <div class="cards">
    <div class="card"><div class="label">Success rate</div>
      <div class="value">{{ report.overview.stats.overall_success_rate }}%</div></div>
    <div class="card"><div class="label">Techniques</div>
      <div class="value">{{ report.overview.stats.total_techniques }}</div></div>
    <div class="card"><div class="label">Attack results</div>
      <div class="value">{{ report.overview.stats.total_results }}</div></div>
    <div class="card"><div class="label">Objectives</div>
      <div class="value">{{ report.overview.stats.unique_objectives }}</div></div>
  </div>

  <h2>Target</h2>
  <table>
    <tr><th>Type</th><td>{{ report.overview.target.type or "Unknown" }}</td></tr>
    <tr><th>Model</th><td>{{ report.overview.target.model or "Unknown" }}</td></tr>
    <tr><th>Endpoint</th><td>{{ report.overview.target.endpoint or "Unknown" }}</td></tr>
  </table>

  <h2>Per-group breakdown</h2>
  <table>
    <tr><th>Group</th><th>Results</th><th>Success rate</th></tr>
    {% for g in report.overview.groups %}
    <tr><td>{{ g.name }}</td><td>{{ g.num_results }}</td><td>{{ g.success_rate }}%</td></tr>
    {% endfor %}
  </table>

  <h2>Conversations ({{ report.conversations | length }})</h2>
  {% for c in report.conversations %}
  <details class="attack">
    <summary class="outcome-{{ c.outcome }}">[{{ c.outcome | upper }}] {{ c.technique }} ·
      turns={{ c.executed_turns }} · score={{ c.score if c.score is not none else "—" }}</summary>
    <div class="muted">id: {{ c.id }}</div>
    <p><strong>Objective:</strong> {{ c.objective }}</p>
    {% for m in c.messages %}
    <div class="msg">
      <div class="role">{{ m.role }}{% if m.is_simulated %} (simulated){% endif %}</div>
      {% for p in m.pieces %}
        {% if p.data_type == "reasoning" %}
          <div class="piece reasoning">{{ p.reasoning_summary }}</div>
        {% elif p.data_type == "text" %}
          <div class="piece">{{ p.converted_value or p.original_value }}</div>
        {% else %}
          <div class="piece media">[{{ p.data_type }}] {{ p.converted_value or p.original_value }}</div>
        {% endif %}
        {% if p.response_error and p.response_error != "none" %}
          <div class="piece err">error: {{ p.response_error }}</div>
        {% endif %}
        {% if p.partial_content %}
          <div class="piece partial">Partial content (before filter triggered): {{ p.partial_content }}</div>
        {% endif %}
        {% for s in p.scores or [] %}
          <div class="score">score: {{ s.scorer }} = {{ s.score_value }}{% if s.score_rationale %}
            — {{ s.score_rationale }}{% endif %}</div>
        {% endfor %}
      {% endfor %}
    </div>
    {% endfor %}
  </details>
  {% endfor %}
</body>
</html>
"""


class HtmlScenarioReportPrinter(PrinterBase):
    """
    Renders the ``full`` scenario report (overview + conversations) as a standalone HTML file.

    Consumes the same format-agnostic payload the JSON ``full`` document serializes
    (``build_scenario_full_payload``); JSON dumps it, this templates it. Text-only:
    non-text pieces (images, audio) render as a ``[data_type] reference`` placeholder,
    not inline media.
    """

    async def render_async(self, payload: dict[str, Any]) -> str:
        """
        Render a ``full`` report payload as an HTML document.

        Args:
            payload (dict[str, Any]): The structure from ``build_scenario_full_payload``.

        Returns:
            str: The rendered HTML document.
        """
        from jinja2 import Environment

        template = Environment(autoescape=True).from_string(_REPORT_TEMPLATE)
        return template.render(report=payload)
