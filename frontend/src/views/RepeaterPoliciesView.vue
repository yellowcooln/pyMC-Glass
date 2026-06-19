<template>
  <div class="space-y-5">
    <header class="page-header">
      <div>
        <h1 class="section-title">Repeater Runtime Policy</h1>
        <p class="section-subtitle">
          Build and queue repeater packet-policy templates for future repeater-side policy_sync support.
        </p>
      </div>
      <div class="header-actions">
        <button class="btn btn-secondary" :disabled="loading" @click="loadAll">Refresh</button>
        <button class="btn btn-primary" :disabled="!canOperate" @click="startNewTemplate">New template</button>
      </div>
    </header>

    <section class="grid-2">
      <UiPanelCard title="Policy Templates" subtitle="Saved Glass-side packet policy documents.">
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

      <UiPanelCard title="Template Editor" subtitle="Edit JSON now; repeater-side execution can be wired later.">
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
          <label class="field-label">
            Policy JSON
            <textarea v-model="form.policyJson" class="field-textarea policy-json" spellcheck="false" />
          </label>
          <div v-if="validation" class="section-subtitle" :class="validation.valid ? 'text-emerald-300' : 'text-rose-300'">
            {{ validation.valid ? "Policy is valid." : validation.errors.join("; ") }}
          </div>
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
        </form>
      </UiPanelCard>
    </section>

    <UiPanelCard title="Queue policy_sync" subtitle="Queues policy_sync commands; repeaters will need command support before execution succeeds.">
      <div class="grid-2">
        <label class="field-label">
          Target template
          <select v-model="syncForm.templateId" class="field">
            <option value="">Use editor policy JSON</option>
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

const DEFAULT_POLICY = {
  enabled: true,
  default_action: "allow",
  rules: [],
  objects: {
    channel_hash_groups: {},
    pubkey_groups: {},
  },
};

const loading = ref(false);
const templates = ref<RepeaterPolicyTemplateResponse[]>([]);
const syncStatuses = ref<RepeaterPolicySyncStatusResponse[]>([]);
const selectedTemplate = ref<RepeaterPolicyTemplateResponse | null>(null);
const validation = ref<RepeaterPolicyValidateResponse | null>(null);

const form = reactive({
  name: "",
  description: "",
  enabled: true,
  policyJson: JSON.stringify(DEFAULT_POLICY, null, 2),
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
  validation.value = null;
}

function selectTemplate(template: RepeaterPolicyTemplateResponse): void {
  selectedTemplate.value = template;
  form.name = template.name;
  form.description = template.description || "";
  form.enabled = template.enabled;
  form.policyJson = JSON.stringify(template.policy, null, 2);
  syncForm.templateId = template.id;
  validation.value = null;
}

function parsePolicy(): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(form.policyJson);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      showErrorToast("Policy JSON must be an object.");
      return null;
    }
    return parsed as Record<string, unknown>;
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

function ruleCount(policy: Record<string, unknown>): number {
  return Array.isArray(policy.rules) ? policy.rules.length : 0;
}
</script>

<style scoped>
.policy-json {
  min-height: 22rem;
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
</style>
