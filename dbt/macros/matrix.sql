{#
  Matrix decomposition helpers (M5.3). A matrix question expands into one
  single-select sub-question per cell (row × column); its stable_name is the
  matrix name joined to the row (and, for matrixdropdown, the column) by '.'.
  This MUST be computed identically in int_survey_questions (the sub-question's
  identity) and int_survey_options (the options scoped to it) or the
  question_version_id surrogate keys won't match and no matrix answer would
  resolve to an option — centralized here so the two can't drift. The '.'
  separator mirrors SurveyJS's own `{matrix.row.column}` reference syntax;
  uniqueness of the components is enforced at publish (validation._validate_matrix).
#}
{% macro subquestion_name(parts) -%}
{% for part in parts %}({{ part }}){% if not loop.last %} || '.' || {% endif %}{% endfor %}
{%- endmacro %}

{#
  Normalize a SurveyJS row/column/choice element to its scalar value text,
  matching int_survey_options: an object keys on 'value'; a bare scalar is
  itself (`#>> '{}'` extracts a jsonb scalar as unquoted text).
#}
{% macro matrix_value(expr) -%}
case when jsonb_typeof({{ expr }}) = 'object' then {{ expr }} ->> 'value' else {{ expr }} #>> '{}' end
{%- endmacro %}
