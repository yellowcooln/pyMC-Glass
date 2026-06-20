<template>
  <div class="space-y-5">
    <header class="page-header">
      <div>
        <h1 class="section-title">Repeater Runtime Policy</h1>
        <p class="section-subtitle">
          Build repeater Policy Engine templates with a form editor, validate them, and queue policy_sync to repeaters.
        </p>
      </div>
      <div class="header-actions">
        <button class="btn btn-secondary" :disabled="loading" @click="loadAll">Refresh</button>
        <button class="btn btn-primary" :disabled="!canOperate" @click="startNewTemplate">New template</button>
      </div>
    </header>

    <section class="grid-2">
      <UiPanelCard title="Policy Templates" subtitle="Saved Glass-side repeater Policy Engine documents.">
        <div v-if="templates.length === 0" class="empty-state">No repeater policy templates yet.</div>
        <div v-else class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Status</th>
                <th>Rules</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="template in templates"
                :key="template.id"
                class="clickable-row"
                :class="{ selected: selectedTemplate?.id === template.id }"
                @click="selectTemplate(template)"
              >
                <td>
                  <strong>{{ template.name }}</strong>
                  <span class="muted-block">{{ template.description || "No description" }}</span>
                </td>
                <td><StatusPill :status="template.enabled ? 'enabled' : 'disabled'" /></td>
                <td>{{ ruleCount(template.policy) }}</td>
                <td>{{ formatTimestamp(template.updated_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </UiPanelCard>

      <UiPanelCard title="Template Details" subtitle="Name the policy template and choose how it appears in Glass.">
        <form class="panel-form" @submit.prevent="saveTemplate">
          <label class="field-label">
            Template name
            <input v-model.trim="form.name" class="field" required />
          </label>
          <label class="field-label">
            Description
            <input v-model.trim="form.description" class="field" />
          </label>
          <label class="toggle-row">
            <input v-model="form.enabled" type="checkbox" />
            <span>Template enabled</span>
          </label>

          <div class="settings-actions">
            <button class="btn btn-secondary" type="button" :disabled="loading" @click="validateCurrentPolicy">
              Validate
            </button>
            <button class="btn btn-primary" :disabled="loading || !canOperate">
              {{ selectedTemplate ? "Save template" : "Create template" }}
            </button>
            <button
              v-if="selectedTemplate"
              class="btn btn-danger"
              type="button"
              :disabled="loading || !canOperate"
              @click="deleteSelectedTemplate"
            >
              Delete
            </button>
          </div>
          <div v-if="validation" class="section-subtitle" :class="validation.valid ? 'text-emerald-300' : 'text-rose-300'">
            {{ validation.valid ? "Policy is valid." : validation.errors.join("; ") }}
          </div>
        </form>
      </UiPanelCard>
    </section>

    <UiPanelCard title="Policy Engine Editor" subtitle="Form editor for the repeater policy_engine object. JSON mode is still available for advanced fields.">
      <div class="editor-mode-row">
        <button
          class="btn"
          :class="editorMode === 'builder' ? 'btn-primary' : 'btn-secondary'"
          type="button"
          @click="switchEditorMode('builder')"
        >
          Visual editor
        </button>
        <button
          class="btn"
          :class="editorMode === 'json' ? 'btn-primary' : 'btn-secondary'"
          type="button"
          @click="switchEditorMode('json')"
        >
          JSON editor
        </button>
      </div>

      <div v-if="editorMode === 'builder'" class="policy-builder">
        <section class="policy-builder-section">
          <div class="grid-2">
            <label class="toggle-row builder-toggle">
              <input v-model="builder.enabled" type="checkbox" @change="syncJsonFromBuilder" />
              <span>Policy Engine enabled</span>
            </label>
            <label class="field-label">
              Default action when no rule matches
              <select v-model="builder.defaultAction" class="field" @change="syncJsonFromBuilder">
                <option value="allow">allow</option>
                <option value="drop">drop</option>
                <option value="log_only">log_only</option>
              </select>
            </label>
          </div>
        </section>

        <section class="policy-builder-section">
          <div class="policy-section-header">
            <div>
              <h3 class="policy-section-title">Add Policy Rule</h3>
              <p class="section-subtitle">Choose match logic, then add one or more conditions. ALL conditions must match.</p>
            </div>
            <button class="btn btn-secondary" type="button" @click="addRule">Add Policy Rule</button>
          </div>

          <div v-if="builder.rules.length === 0" class="empty-state">No rules. Default action will apply.</div>
          <article v-for="(rule, ruleIndex) in builder.rules" :key="rule.localId" class="rule-card repeater-style-rule">
            <div class="repeater-rule-grid">
              <label class="field-label">
                Rule Name
                <input v-model.trim="rule.name" class="field" @input="syncJsonFromBuilder" />
              </label>
              <label class="field-label">
                Match Logic
                <select v-model="rule.logic" class="field" @change="syncJsonFromBuilder">
                  <option value="all">ALL</option>
                  <option value="any">ANY</option>
                </select>
              </label>
              <label class="field-label">
                Action
                <select v-model="rule.action" class="field" @change="syncJsonFromBuilder">
                  <option value="allow">allow</option>
                  <option value="drop">drop</option>
                  <option value="log_only">log_only</option>
                </select>
              </label>
              <label class="toggle-row builder-toggle">
                <input v-model="rule.enabled" type="checkbox" @change="syncJsonFromBuilder" />
                <span>Rule enabled</span>
              </label>
            </div>

            <div class="condition-list repeater-conditions">
              <div class="policy-section-header compact-header">
                <h4 class="condition-title">Conditions</h4>
                <div class="rule-actions">
                  <button class="btn btn-secondary" type="button" @click="addCondition(ruleIndex)">Add condition</button>
                  <button class="btn btn-danger" type="button" @click="removeRule(ruleIndex)">Remove rule</button>
                </div>
              </div>
              <div v-if="rule.conditions.length === 0" class="empty-state small">Add at least one condition for this rule to match.</div>
              <div v-else class="conditions-table-wrap">
                <table class="data-table conditions-table">
                  <thead>
                    <tr>
                      <th>Drag</th>
                      <th>Field</th>
                      <th>Operator</th>
                      <th>Source</th>
                      <th>Value</th>
                      <th>Type</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(condition, conditionIndex) in rule.conditions" :key="condition.localId">
                      <td class="drag-cell">
                        <button class="mini-btn" type="button" :disabled="conditionIndex === 0" @click="moveCondition(ruleIndex, conditionIndex, -1)">↑</button>
                        <button class="mini-btn" type="button" :disabled="conditionIndex === rule.conditions.length - 1" @click="moveCondition(ruleIndex, conditionIndex, 1)">↓</button>
                      </td>
                      <td>
                        <select v-model="condition.field" class="field table-field" @change="syncJsonFromBuilder">
                          <option v-for="field in fieldOptions" :key="field.value" :value="field.value">{{ field.label }}</option>
                        </select>
                      </td>
                      <td>
                        <select v-model="condition.op" class="field table-field" @change="syncJsonFromBuilder">
                          <option v-for="op in operatorOptions" :key="op.value" :value="op.value">{{ op.label }}</option>
                        </select>
                      </td>
                      <td>
                        <select v-model="condition.valueSource" class="field table-field" @change="syncJsonFromBuilder">
                          <option value="literal">Literal</option>
                          <option value="group">Group</option>
                        </select>
                      </td>
                      <td>
                        <input
                          v-model="condition.value"
                          class="field table-field value-field"
                          :placeholder="condition.valueSource === 'group' ? '@channel_hash_groups.blocked' : 'value'"
                          @input="syncJsonFromBuilder"
                        />
                      </td>
                      <td>
                        <select v-model="condition.valueType" class="field table-field" @change="syncJsonFromBuilder">
                          <option value="string">String</option>
                          <option value="number">Number</option>
                          <option value="boolean">Boolean</option>
                        </select>
                      </td>
                      <td>
                        <button class="btn btn-danger condition-remove" type="button" @click="removeCondition(ruleIndex, conditionIndex)">
                          Remove
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </article>
        </section>

        <section class="policy-builder-section">
          <div class="policy-section-header">
            <div>
              <h3 class="policy-section-title">Objects</h3>
              <p class="section-subtitle">Named groups referenced from condition values, e.g. <code>@channel_hash_groups.blocked</code>.</p>
            </div>
          </div>
          <textarea v-model="builder.objectsJson" class="field-textarea objects-json" spellcheck="false" @input="syncJsonFromBuilder" />
        </section>
      </div>

      <div v-else>
        <label class="field-label">
          Policy Engine JSON
          <textarea v-model="form.policyJson" class="field-textarea policy-json" spellcheck="false" @blur="loadBuilderFromJson" />
        </label>
      </div>
    </UiPanelCard>

    <UiPanelCard title="Queue policy_sync" subtitle="Queues policy_sync commands; repeaters need policy_sync support before execution succeeds.">
      <div class="grid-2">
        <label class="field-label">
          Target template
          <select v-model="syncForm.templateId" class="field">
            <option value="">Use editor policy</option>
            <option v-for="template in templates" :key="template.id" :value="template.id">
              {{ template.name }}
            </option>
          </select>
        </label>
        <label class="field-label">
          Mode
          <select v-model="syncForm.mode" class="field">
            <option value="replace">replace</option>
            <option value="patch">patch</option>
          </select>
        </label>
      </div>
      <label class="toggle-row">
        <input v-model="syncForm.allRepeaters" type="checkbox" />
        <span>Queue to all eligible repeaters</span>
      </label>
      <label class="toggle-row">
        <input v-model="syncForm.validateOnly" type="checkbox" />
        <span>Validate-only command</span>
      </label>
      <div v-if="!syncForm.allRepeaters" class="checkbox-grid">
        <label v-for="repeater in appState.repeaters" :key="repeater.id" class="toggle-row compact">
          <input v-model="syncForm.repeaterIds" type="checkbox" :value="repeater.id" />
          <span>{{ repeater.node_name }} <small class="text-slate-500">({{ repeater.status }})</small></span>
        </label>
      </div>
      <label class="field-label">
        Reason
        <input v-model.trim="syncForm.reason" class="field" placeholder="e.g. initial packet policy rollout" />
      </label>
      <div class="settings-actions">
        <button class="btn btn-danger" :disabled="loading || !canOperate" @click="queueSync">Queue policy_sync</button>
      </div>
    </UiPanelCard>

    <UiPanelCard title="Sync Status" subtitle="Latest queued policy sync per repeater.">
      <div v-if="syncStatuses.length === 0" class="empty-state">No policy sync commands queued yet.</div>
      <div v-else class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>Repeater</th>
              <th>Status</th>
              <th>Command</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="status in syncStatuses" :key="status.repeater_id">
              <td>{{ status.node_name }}</td>
              <td><StatusPill :status="status.status" /></td>
              <td class="mono-value">{{ status.command_id || "—" }}</td>
              <td>{{ formatTimestamp(status.updated_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </UiPanelCard>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";

import {
  createRepeaterPolicyTemplate,
  deleteRepeaterPolicyTemplate,
  listRepeaterPolicySyncStatus,
  listRepeaterPolicyTemplates,
  syncRepeaterPolicy,
  updateRepeaterPolicyTemplate,
  validateRepeaterPolicy,
} from "../api";
import UiPanelCard from "../components/ui/UiPanelCard.vue";
import StatusPill from "../components/ui/StatusPill.vue";
import { appState, canOperate, formatTimestamp, showErrorToast, showSuccessToast } from "../state/appState";
import type {
  RepeaterPolicySyncStatusResponse,
  RepeaterPolicyTemplateResponse,
  RepeaterPolicyValidateResponse,
} from "../types";

type Action = "allow" | "drop" | "log_only";
type Logic = "all" | "any";
type EditorMode = "builder" | "json";
type RuleValueType = "string" | "number" | "boolean";
type ValueSource = "literal" | "group";

type BuilderCondition = {
  localId: string;
  field: string;
  op: string;
  value: string;
  valueType: RuleValueType;
  valueSource: ValueSource;
  groupKind?: "channel_hashes" | "pubkeys";
  groupId?: string;
};

type BuilderRule = {
  localId: string;
  id: string;
  name: string;
  enabled: boolean;
  logic: Logic;
  action: Action;
  conditions: BuilderCondition[];
};

const DEFAULT_POLICY = {
  enabled: true,
  default_action: "allow",
  rules: [],
  objects: {
    channel_hash_groups: {},
    pubkey_groups: {},
  },
};

const fieldOptions = [
  { value: "route_type", label: "Route Type" },
  { value: "payload_type", label: "Payload Type" },
  { value: "payload_length", label: "Payload Length" },
  { value: "path_hash_size", label: "Path Hash Size" },
  { value: "hop_count", label: "Hop Count" },
  { value: "rssi", label: "RSSI" },
  { value: "snr", label: "SNR" },
  { value: "mode", label: "Mode" },
  { value: "local_transmission", label: "Local Transmission" },
  { value: "path_hashes", label: "Path Hashes" },
  { value: "channel_hash", label: "Channel Hash" },
  { value: "channel_decryptable", label: "Channel Decryptable" },
  { value: "channel_message_body", label: "Channel Message Body" },
  { value: "payload_hex", label: "Payload Hex" },
  { value: "transport_code_0", label: "Transport Code 0" },
  { value: "transport_code_1", label: "Transport Code 1" },
];

const operatorOptions = [
  { value: "equals", label: "Equals" },
  { value: "not_equals", label: "Not Equals" },
  { value: "greater_than", label: "Greater Than" },
  { value: "greater_or_equal", label: "Greater or Equal" },
  { value: "less_than", label: "Less Than" },
  { value: "less_or_equal", label: "Less or Equal" },
  { value: "contains", label: "Contains" },
  { value: "in", label: "In List" },
  { value: "intersects", label: "Intersects" },
  { value: "starts_with", label: "Starts With" },
  { value: "ends_with", label: "Ends With" },
];

const loading = ref(false);
const templates = ref<RepeaterPolicyTemplateResponse[]>([]);
const syncStatuses = ref<RepeaterPolicySyncStatusResponse[]>([]);
const selectedTemplate = ref<RepeaterPolicyTemplateResponse | null>(null);
const validation = ref<RepeaterPolicyValidateResponse | null>(null);
const editorMode = ref<EditorMode>("builder");

const form = reactive({
  name: "",
  description: "",
  enabled: true,
  policyJson: JSON.stringify(DEFAULT_POLICY, null, 2),
});

const builder = reactive({
  enabled: true,
  defaultAction: "allow" as Action,
  objectsJson: JSON.stringify(DEFAULT_POLICY.objects, null, 2),
  rules: [] as BuilderRule[],
});

const syncForm = reactive({
  templateId: "",
  allRepeaters: true,
  repeaterIds: [] as string[],
  mode: "replace" as "replace" | "patch",
  validateOnly: false,
  reason: "",
});

onMounted(() => {
  loadBuilderFromJson({ silent: true });
  void loadAll();
});

async function loadAll(): Promise<void> {
  if (!appState.token) return;
  loading.value = true;
  try {
    const [templateRows, statusRows] = await Promise.all([
      listRepeaterPolicyTemplates(appState.token),
      listRepeaterPolicySyncStatus(appState.token),
    ]);
    templates.value = templateRows;
    syncStatuses.value = statusRows;
  } catch (error) {
    showErrorToast(error instanceof Error ? error.message : "Failed to load repeater policies");
  } finally {
    loading.value = false;
  }
}

function startNewTemplate(): void {
  selectedTemplate.value = null;
  form.name = "";
  form.description = "";
  form.enabled = true;
  form.policyJson = JSON.stringify(DEFAULT_POLICY, null, 2);
  syncForm.templateId = "";
  validation.value = null;
  loadBuilderFromJson({ silent: true });
}

function selectTemplate(template: RepeaterPolicyTemplateResponse): void {
  selectedTemplate.value = template;
  form.name = template.name;
  form.description = template.description || "";
  form.enabled = template.enabled;
  form.policyJson = JSON.stringify(template.policy, null, 2);
  syncForm.templateId = template.id;
  validation.value = null;
  loadBuilderFromJson({ silent: true });
}

function switchEditorMode(mode: EditorMode): void {
  if (mode === editorMode.value) return;
  if (mode === "builder") {
    loadBuilderFromJson();
  } else {
    syncJsonFromBuilder();
  }
  editorMode.value = mode;
}

function parsePolicy(): Record<string, unknown> | null {
  if (editorMode.value === "builder") {
    const policy = policyFromBuilder();
    if (!policy) return null;
    form.policyJson = JSON.stringify(policy, null, 2);
    return policy;
  }

  try {
    const parsed = JSON.parse(form.policyJson);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      showErrorToast("Policy JSON must be an object.");
      return null;
    }
    return unwrapPolicyEngine(parsed as Record<string, unknown>);
  } catch (error) {
    showErrorToast(error instanceof Error ? error.message : "Policy JSON is invalid.");
    return null;
  }
}

async function validateCurrentPolicy(): Promise<boolean> {
  if (!appState.token) return false;
  const policy = parsePolicy();
  if (!policy) return false;
  try {
    validation.value = await validateRepeaterPolicy(appState.token, { policy });
    if (!validation.value.valid) {
      showErrorToast(validation.value.errors.join("; "));
      return false;
    }
    showSuccessToast("Repeater policy is valid.");
    return true;
  } catch (error) {
    showErrorToast(error instanceof Error ? error.message : "Policy validation failed");
    return false;
  }
}

async function saveTemplate(): Promise<void> {
  if (!appState.token) return;
  const policy = parsePolicy();
  if (!policy) return;
  loading.value = true;
  try {
    if (selectedTemplate.value) {
      const updated = await updateRepeaterPolicyTemplate(appState.token, selectedTemplate.value.id, {
        name: form.name,
        description: form.description || null,
        enabled: form.enabled,
        policy,
      });
      selectedTemplate.value = updated;
      showSuccessToast("Repeater policy template saved.");
    } else {
      const created = await createRepeaterPolicyTemplate(appState.token, {
        name: form.name,
        description: form.description || null,
        enabled: form.enabled,
        policy,
      });
      selectedTemplate.value = created;
      syncForm.templateId = created.id;
      showSuccessToast("Repeater policy template created.");
    }
    await loadAll();
  } catch (error) {
    showErrorToast(error instanceof Error ? error.message : "Failed to save repeater policy template");
  } finally {
    loading.value = false;
  }
}

async function deleteSelectedTemplate(): Promise<void> {
  if (!appState.token || !selectedTemplate.value) return;
  loading.value = true;
  try {
    await deleteRepeaterPolicyTemplate(appState.token, selectedTemplate.value.id);
    showSuccessToast("Repeater policy template deleted.");
    startNewTemplate();
    await loadAll();
  } catch (error) {
    showErrorToast(error instanceof Error ? error.message : "Failed to delete repeater policy template");
  } finally {
    loading.value = false;
  }
}

async function queueSync(): Promise<void> {
  if (!appState.token) return;
  const policy = syncForm.templateId ? undefined : parsePolicy();
  if (!syncForm.templateId && !policy) return;
  loading.value = true;
  try {
    const result = await syncRepeaterPolicy(appState.token, {
      template_id: syncForm.templateId || undefined,
      policy,
      all_repeaters: syncForm.allRepeaters,
      repeater_ids: syncForm.allRepeaters ? [] : syncForm.repeaterIds,
      mode: syncForm.mode,
      validate_only: syncForm.validateOnly,
      reason: syncForm.reason || undefined,
    });
    showSuccessToast(`Queued ${result.queued_commands} policy_sync command${result.queued_commands === 1 ? "" : "s"}.`);
    await loadAll();
  } catch (error) {
    showErrorToast(error instanceof Error ? error.message : "Failed to queue policy sync");
  } finally {
    loading.value = false;
  }
}

function addRule(): void {
  const nextNumber = builder.rules.length + 1;
  builder.rules.push({
    localId: crypto.randomUUID(),
    id: `glass-rule-${nextNumber}`,
    name: `Rule ${nextNumber}`,
    enabled: true,
    logic: "all",
    action: "drop",
    conditions: [newCondition()],
  });
  syncJsonFromBuilder();
}

function removeRule(index: number): void {
  builder.rules.splice(index, 1);
  syncJsonFromBuilder();
}

function addCondition(ruleIndex: number): void {
  builder.rules[ruleIndex]?.conditions.push(newCondition());
  syncJsonFromBuilder();
}

function removeCondition(ruleIndex: number, conditionIndex: number): void {
  builder.rules[ruleIndex]?.conditions.splice(conditionIndex, 1);
  syncJsonFromBuilder();
}

function moveCondition(ruleIndex: number, conditionIndex: number, delta: number): void {
  const conditions = builder.rules[ruleIndex]?.conditions;
  if (!conditions) return;
  const nextIndex = conditionIndex + delta;
  if (nextIndex < 0 || nextIndex >= conditions.length) return;
  const [item] = conditions.splice(conditionIndex, 1);
  conditions.splice(nextIndex, 0, item);
  syncJsonFromBuilder();
}

function newCondition(): BuilderCondition {
  return {
    localId: crypto.randomUUID(),
    field: "hop_count",
    op: "greater_than",
    value: "2",
    valueType: "number",
    valueSource: "literal",
  };
}

function syncJsonFromBuilder(): void {
  const policy = policyFromBuilder({ quiet: true });
  if (policy) {
    form.policyJson = JSON.stringify(policy, null, 2);
  }
  validation.value = null;
}

function loadBuilderFromJson(options: { silent?: boolean } = {}): boolean {
  try {
    const parsed = JSON.parse(form.policyJson);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("Policy JSON must be an object.");
    }
    applyPolicyToBuilder(unwrapPolicyEngine(parsed as Record<string, unknown>));
    return true;
  } catch (error) {
    if (!options.silent) {
      showErrorToast(error instanceof Error ? error.message : "Failed to load JSON into visual editor");
    }
    return false;
  }
}

function policyFromBuilder(options: { quiet?: boolean } = {}): Record<string, unknown> | null {
  let objects: Record<string, unknown>;
  try {
    const parsedObjects = JSON.parse(builder.objectsJson || "{}");
    if (!parsedObjects || typeof parsedObjects !== "object" || Array.isArray(parsedObjects)) {
      throw new Error("Objects must be a JSON object.");
    }
    objects = parsedObjects as Record<string, unknown>;
  } catch (error) {
    if (!options.quiet) {
      showErrorToast(error instanceof Error ? error.message : "Objects JSON is invalid.");
    }
    return null;
  }

  return {
    enabled: builder.enabled,
    default_action: builder.defaultAction,
    rules: builder.rules.map((rule, index) => ({
      id: rule.id || `glass-rule-${index + 1}`,
      name: rule.name || `Rule ${index + 1}`,
      enabled: rule.enabled,
      if: {
        [rule.logic]: rule.conditions.map((condition) => ({
          field: condition.field,
          op: condition.op,
          value: conditionValueForPolicy(condition),
        })),
      },
      then: { action: rule.action },
    })),
    objects,
  };
}

function applyPolicyToBuilder(policy: Record<string, unknown>): void {
  builder.enabled = Boolean(policy.enabled ?? true);
  builder.defaultAction = isAction(policy.default_action) ? policy.default_action : "allow";
  const objects = policy.objects && typeof policy.objects === "object" && !Array.isArray(policy.objects) ? policy.objects : {};
  builder.objectsJson = JSON.stringify(objects, null, 2);
  const rules = Array.isArray(policy.rules) ? policy.rules : [];
  builder.rules.splice(0, builder.rules.length, ...rules.map(toBuilderRule));
}

function toBuilderRule(value: unknown, index: number): BuilderRule {
  const rule = value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
  const conditionBlock = rule.if && typeof rule.if === "object" && !Array.isArray(rule.if) ? (rule.if as Record<string, unknown>) : {};
  const logic: Logic = Array.isArray(conditionBlock.any) ? "any" : "all";
  const rawConditions = Array.isArray(conditionBlock[logic]) ? conditionBlock[logic] : [];
  const thenBlock = rule.then && typeof rule.then === "object" && !Array.isArray(rule.then) ? (rule.then as Record<string, unknown>) : {};
  const actionCandidate = thenBlock.action ?? rule.action;
  return {
    localId: crypto.randomUUID(),
    id: String(rule.id ?? `glass-rule-${index + 1}`),
    name: String(rule.name ?? `Rule ${index + 1}`),
    enabled: Boolean(rule.enabled ?? true),
    logic,
    action: isAction(actionCandidate) ? actionCandidate : "allow",
    conditions: rawConditions.map(toBuilderCondition),
  };
}

function toBuilderCondition(value: unknown): BuilderCondition {
  const condition = value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
  const rawValue = condition.value;
  const groupRef = typeof rawValue === "string" && rawValue.startsWith("@");
  return {
    localId: crypto.randomUUID(),
    field: String(condition.field ?? "hop_count"),
    op: String(condition.op ?? condition.operator ?? "equals"),
    value: stringifyConditionValue(rawValue),
    valueType: inferValueType(rawValue),
    valueSource: groupRef ? "group" : "literal",
  };
}

function conditionValueForPolicy(condition: BuilderCondition): unknown {
  if (condition.valueSource === "group") {
    return condition.value.trim().startsWith("@") ? condition.value.trim() : `@${condition.value.trim()}`;
  }
  return parseConditionValue(condition.value, condition.valueType);
}

function parseConditionValue(value: string, valueType: RuleValueType): unknown {
  const trimmed = value.trim();
  if (!trimmed) return "";
  if (valueType === "boolean") return trimmed === "true";
  if (valueType === "number") {
    const parsed = Number(trimmed);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return trimmed;
}

function inferValueType(value: unknown): RuleValueType {
  if (typeof value === "boolean") return "boolean";
  if (typeof value === "number") return "number";
  return "string";
}

function stringifyConditionValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === undefined) return "";
  return JSON.stringify(value);
}

function unwrapPolicyEngine(policy: Record<string, unknown>): Record<string, unknown> {
  const nested = policy.policy_engine;
  if (nested && typeof nested === "object" && !Array.isArray(nested)) {
    return nested as Record<string, unknown>;
  }
  return policy;
}

function isAction(value: unknown): value is Action {
  return value === "allow" || value === "drop" || value === "log_only";
}

function ruleCount(policy: Record<string, unknown>): number {
  return Array.isArray(policy.rules) ? policy.rules.length : 0;
}
</script>

<style scoped>
.policy-json {
  min-height: 22rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
}

.objects-json {
  min-height: 12rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
}

.clickable-row {
  cursor: pointer;
}

.clickable-row.selected {
  background: rgba(34, 211, 238, 0.08);
}

.muted-block {
  display: block;
  color: rgb(148 163 184);
  font-size: 0.8rem;
  margin-top: 0.25rem;
}

.checkbox-grid {
  display: grid;
  gap: 0.5rem;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}

.toggle-row.compact {
  margin: 0;
}

.editor-mode-row,
.policy-section-header,
.rule-header,
.rule-actions,
.rule-title-wrap {
  align-items: center;
  display: flex;
  gap: 0.75rem;
}

.editor-mode-row,
.policy-section-header,
.rule-header {
  justify-content: space-between;
}

.policy-builder {
  display: grid;
  gap: 1rem;
}

.policy-builder-section,
.rule-card {
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 1rem;
  padding: 1rem;
  background: rgba(15, 23, 42, 0.28);
}

.policy-section-title,
.condition-title {
  color: rgb(226 232 240);
  font-weight: 700;
  margin: 0;
}

.builder-toggle {
  align-self: end;
  min-height: 2.75rem;
}

.rule-title-wrap {
  flex: 1;
}

.rule-number {
  align-items: center;
  background: rgba(34, 211, 238, 0.15);
  border: 1px solid rgba(34, 211, 238, 0.35);
  border-radius: 999px;
  color: rgb(103 232 249);
  display: inline-flex;
  font-weight: 700;
  height: 2rem;
  justify-content: center;
  width: 2rem;
}

.grow {
  flex: 1;
}

.condition-list {
  display: grid;
  gap: 0.75rem;
  margin-top: 1rem;
}

.compact-header {
  margin-bottom: -0.25rem;
}

.condition-row {
  align-items: end;
  display: grid;
  gap: 0.75rem;
  grid-template-columns: minmax(160px, 1fr) minmax(140px, 0.8fr) minmax(180px, 1fr) auto;
}

.condition-remove {
  margin-bottom: 0.1rem;
}

.empty-state.small {
  padding: 0.7rem;
}

code {
  color: rgb(103 232 249);
}

@media (max-width: 900px) {
  .rule-header,
  .policy-section-header,
  .rule-actions,
  .rule-title-wrap,
  .editor-mode-row {
    align-items: stretch;
    flex-direction: column;
  }

  .condition-row {
    grid-template-columns: 1fr;
  }
}
</style>
