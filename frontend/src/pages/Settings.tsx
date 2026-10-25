import { useEffect, useMemo, useState } from "react";

import AccountTreeOutlinedIcon from "@mui/icons-material/AccountTreeOutlined";
import DeleteIcon from "@mui/icons-material/Delete";
import AddIcon from "@mui/icons-material/Add";
import WarningAmberOutlinedIcon from "@mui/icons-material/WarningAmberOutlined";
import SecurityOutlinedIcon from "@mui/icons-material/SecurityOutlined";
import AssignmentOutlinedIcon from "@mui/icons-material/AssignmentOutlined";
import {
  addWorkflowRule,
  deleteWorkflowRule,
  getWorkflowConfig,
  getWorkflowOptions,
  updateWorkflowConfig,
} from "../api/auth";
import type { WorkflowConfig, WorkflowOptions, WorkflowRule } from "../api/auth";
import { useAuth } from "../context/AuthContext";
import {
  WORKFLOW_CONDITION_CONFIDENCE_BELOW,
  optionsForDepartment,
  ruleTitle,
  validateWorkflowRule,
  validateWorkflowRules,
} from "../utils/workflowRouting";

function getErrorMessage(error: unknown, fallback: string) {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object" && "msg" in item) return String((item as { msg: unknown }).msg);
        return "";
      })
      .filter(Boolean);
    if (messages.length) return messages.join(" ");
  }
  return fallback;
}

function toDraftRule(rule: WorkflowRule, fallbackDepartment: string): WorkflowRule {
  return {
    ...rule,
    department: rule.department || fallbackDepartment,
    document_type: rule.document_type || "",
    condition: rule.condition || WORKFLOW_CONDITION_CONFIDENCE_BELOW,
    threshold: rule.threshold ?? 80,
  };
}

export default function Settings() {
  const { user: currentUser, isAdmin, isSuperAdmin, isDeptAdmin } = useAuth();
  const [workflow, setWorkflow] = useState<WorkflowConfig | null>(null);
  const [options, setOptions] = useState<WorkflowOptions | null>(null);
  const [draftThreshold, setDraftThreshold] = useState(80);
  const [draftRules, setDraftRules] = useState<WorkflowRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const canManageRules = isSuperAdmin || isDeptAdmin;
  const canEditThreshold = Boolean(isSuperAdmin && (options?.can_edit_threshold ?? workflow?.can_edit_threshold ?? true));
  const lockedDepartment = isDeptAdmin ? (currentUser?.department || "").trim().toUpperCase() : "";

  useEffect(() => {
    let mounted = true;
    Promise.all([getWorkflowConfig(), getWorkflowOptions()])
      .then(([workflowConfig, workflowOptions]) => {
        if (!mounted) return;
        const fallbackDepartment =
          workflowOptions.selected_department ||
          lockedDepartment ||
          workflowOptions.departments[0]?.value ||
          "";
        setWorkflow(workflowConfig);
        setOptions(workflowOptions);
        setDraftThreshold(workflowConfig.threshold);
        setDraftRules(workflowConfig.rules.map((rule) => toDraftRule(rule, fallbackDepartment)));
      })
      .catch((loadError: unknown) => {
        if (mounted) setError(getErrorMessage(loadError, "Unable to load workflow settings."));
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, [lockedDepartment]);

  const clearFeedback = () => {
    setError(null);
    setSuccess(null);
  };

  const defaultDepartment = useMemo(() => {
    return lockedDepartment || options?.selected_department || options?.departments[0]?.value || "";
  }, [lockedDepartment, options]);

  const updateRule = (index: number, changes: Partial<WorkflowRule>) => {
    setDraftRules((previous) =>
      previous.map((rule, currentIndex) => {
        if (currentIndex !== index) return rule;
        const next = { ...rule, ...changes };
        if (changes.department && changes.department !== rule.department) {
          const scoped = optionsForDepartment(options, changes.department);
          next.document_type = scoped.documentTypes[0]?.value || "";
          next.route = scoped.routes[0]?.value || "";
        }
        if (changes.document_type || changes.threshold || next.document_type) {
          next.title = ruleTitle(next.document_type || "", Number(next.threshold ?? draftThreshold));
        }
        return next;
      })
    );
    clearFeedback();
  };

  const handleAddRule = async () => {
    clearFeedback();
    const department = defaultDepartment;
    const scoped = optionsForDepartment(options, department);
    const documentType = scoped.documentTypes[0]?.value || "";
    const route = scoped.routes[0]?.value || "";
    const type = scoped.ruleTypes[0]?.value || "warning";
    const condition = scoped.conditions[0]?.value || WORKFLOW_CONDITION_CONFIDENCE_BELOW;
    const draft: WorkflowRule = {
      title: ruleTitle(documentType, draftThreshold),
      department,
      document_type: documentType,
      route,
      type,
      condition,
      threshold: draftThreshold,
    };
    const validationError = validateWorkflowRule(draft, options, isSuperAdmin);
    if (validationError) {
      setError(validationError);
      return;
    }
    setWorking(true);
    try {
      const created = await addWorkflowRule({
        title: draft.title,
        route: draft.route,
        type: draft.type,
        threshold: draft.threshold,
        document_type: draft.document_type,
        department: draft.department,
        condition: draft.condition,
      });
      const normalized = toDraftRule(created, department);
      setDraftRules((previous) => [...previous, normalized]);
      setWorkflow((previous) => (previous ? { ...previous, rules: [...previous.rules, created] } : previous));
      setSuccess("Routing rule added.");
    } catch (ruleError: unknown) {
      setError(getErrorMessage(ruleError, "Unable to add a routing rule."));
    } finally {
      setWorking(false);
    }
  };

  const handleDeleteRule = async (rule: WorkflowRule, index: number) => {
    if (!window.confirm(`Remove the routing rule "${rule.title}"?`)) return;
    clearFeedback();
    setWorking(true);
    try {
      if (rule.id !== undefined) await deleteWorkflowRule(rule.id);
      const remaining = draftRules.filter((_, currentIndex) => currentIndex !== index);
      setDraftRules(remaining);
      setWorkflow((previous) => (previous ? { ...previous, rules: remaining } : previous));
      setSuccess("Routing rule removed.");
    } catch (ruleError: unknown) {
      setError(getErrorMessage(ruleError, "Unable to remove this routing rule."));
    } finally {
      setWorking(false);
    }
  };

  const handleSaveWorkflow = async () => {
    clearFeedback();
    const validationError = validateWorkflowRules(draftRules, options, isSuperAdmin);
    if (validationError) {
      setError(validationError);
      return;
    }
    setWorking(true);
    try {
      const updated = await updateWorkflowConfig({
        threshold: canEditThreshold ? draftThreshold : workflow?.threshold ?? draftThreshold,
        rules: draftRules.map((rule) => ({
          ...rule,
          title: ruleTitle(rule.document_type || "", Number(rule.threshold ?? draftThreshold)),
          document_type: rule.document_type?.trim() || null,
          department: isSuperAdmin ? rule.department : lockedDepartment || rule.department,
          condition: rule.condition || WORKFLOW_CONDITION_CONFIDENCE_BELOW,
        })),
      });
      const fallbackDepartment = defaultDepartment;
      setWorkflow(updated);
      setDraftThreshold(updated.threshold);
      setDraftRules(updated.rules.map((rule) => toDraftRule(rule, fallbackDepartment)));
      setSuccess("Workflow settings saved.");
    } catch (workflowError: unknown) {
      setError(getErrorMessage(workflowError, "Unable to save workflow settings."));
    } finally {
      setWorking(false);
    }
  };

  const discardWorkflowChanges = () => {
    if (!workflow) return;
    setDraftThreshold(workflow.threshold);
    setDraftRules(workflow.rules.map((rule) => toDraftRule(rule, defaultDepartment)));
    clearFeedback();
  };

  if (!isAdmin) {
    return <div className="settingsAccessDenied" role="alert">Access denied. Administrator privileges are required.</div>;
  }

  return (
    <div className="settingsPage">
      <div className="settingsHeader">
        <h1>Settings</h1>
        <p>Configure document workflow routing for departments you are authorized to manage</p>
      </div>

      {error && <div className="settingsNotice error" role="alert">{error}<button type="button" onClick={() => setError(null)} aria-label="Dismiss error">×</button></div>}
      {success && <div className="settingsNotice success" role="status">{success}<button type="button" onClick={() => setSuccess(null)} aria-label="Dismiss message">×</button></div>}
      {loading ? (
        <div className="settingsLoading" role="status">Loading settings…</div>
      ) : (
        <section className="settingsSection" aria-label="Document workflow configuration">
          <h2 className="sectionTitle">
            <AccountTreeOutlinedIcon fontSize="small" /> Default confidence threshold
          </h2>
          <div className="thresholdCard">
            <div className="thresholdTop">
              <div>
                <h3>Auto-flag threshold</h3>
                <p>
                  {canEditThreshold
                    ? "Documents below this confidence are sent to the review queue"
                    : "Only Super Admin can change the global auto-flag threshold"}
                </p>
              </div>
              <strong>{draftThreshold}%</strong>
            </div>
            <input
              type="range"
              min="50"
              max="100"
              value={draftThreshold}
              disabled={working || !canEditThreshold}
              onChange={(event) => {
                setDraftThreshold(Number(event.target.value));
                clearFeedback();
              }}
              className="confidenceSlider"
              aria-label="Auto-flag confidence threshold"
            />
            <div className="sliderLabels">
              <span>50%</span>
              <span>75%</span>
              <span>100%</span>
            </div>
          </div>
          <h2 className="sectionTitle routingTitle">Routing rules</h2>
          <div className="routingRules">
            {draftRules.map((rule, index) => {
              const department = isSuperAdmin ? rule.department || defaultDepartment : lockedDepartment || rule.department || defaultDepartment;
              const scoped = optionsForDepartment(options, department);
              const fieldsDisabled = working || !canManageRules;
              return (
                <div className="routingRule" key={rule.id ?? `new-${index}`}>
                  <div className="routingIcon">
                    {rule.type === "security" ? (
                      <SecurityOutlinedIcon />
                    ) : rule.type === "claims" ? (
                      <AssignmentOutlinedIcon />
                    ) : (
                      <WarningAmberOutlinedIcon />
                    )}
                  </div>
                  <div className="routingInfo">
                    <label>
                      Department
                      <select
                        value={department}
                        disabled={fieldsDisabled || !isSuperAdmin}
                        onChange={(event) => updateRule(index, { department: event.target.value })}
                        aria-label={`Department for ${rule.title}`}
                      >
                        <option value="" disabled>
                          Select department
                        </option>
                        {scoped.departments.map((item) => (
                          <option key={item.value} value={item.value}>
                            {item.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Document type
                      <select
                        value={rule.document_type ?? ""}
                        disabled={fieldsDisabled}
                        onChange={(event) => updateRule(index, { document_type: event.target.value })}
                        aria-label={`Document type for ${rule.title}`}
                      >
                        <option value="" disabled>
                          Select document type
                        </option>
                        {scoped.documentTypes.map((item) => (
                          <option key={item.value} value={item.value}>
                            {item.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Route to
                      <select
                        value={rule.route}
                        disabled={fieldsDisabled}
                        onChange={(event) => updateRule(index, { route: event.target.value })}
                        aria-label={`Route to for ${rule.title}`}
                      >
                        <option value="" disabled>
                          Select team or user
                        </option>
                        {scoped.routes.map((item) => (
                          <option key={`${item.department}-${item.value}`} value={item.value}>
                            {item.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <div className="workflowRuleOptions">
                      <label>
                        Rule type
                        <select
                          value={rule.type}
                          disabled={fieldsDisabled}
                          onChange={(event) => updateRule(index, { type: event.target.value })}
                        >
                          {scoped.ruleTypes.map((item) => (
                            <option key={item.value} value={item.value}>
                              {item.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        Workflow condition
                        <select
                          value={rule.condition || WORKFLOW_CONDITION_CONFIDENCE_BELOW}
                          disabled={fieldsDisabled}
                          onChange={(event) => updateRule(index, { condition: event.target.value })}
                        >
                          {scoped.conditions.map((item) => (
                            <option key={item.value} value={item.value}>
                              {item.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        Threshold
                        <input
                          type="number"
                          min="50"
                          max="100"
                          value={rule.threshold ?? draftThreshold}
                          disabled={fieldsDisabled}
                          onChange={(event) => updateRule(index, { threshold: Number(event.target.value) })}
                        />
                      </label>
                    </div>
                  </div>
                  <button
                    className="deleteMember"
                    type="button"
                    title="Remove routing rule"
                    aria-label={`Remove ${rule.title}`}
                    disabled={fieldsDisabled}
                    onClick={() => handleDeleteRule(rule, index)}
                  >
                    <DeleteIcon />
                  </button>
                </div>
              );
            })}
            <button className="addRuleButton" type="button" disabled={working || !canManageRules} onClick={handleAddRule}>
              <AddIcon /> Add routing rule
            </button>
          </div>
          <div className="workflowActions">
            <button className="discardButton" type="button" disabled={working || !workflow} onClick={discardWorkflowChanges}>
              Discard changes
            </button>
            <button className="saveButton" type="button" disabled={working || !canManageRules} onClick={handleSaveWorkflow}>
              {working ? "Saving…" : "Save changes"}
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
