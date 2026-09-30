import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import GroupIcon from "@mui/icons-material/Group";
import ShieldOutlinedIcon from "@mui/icons-material/ShieldOutlined";
import AccountTreeOutlinedIcon from "@mui/icons-material/AccountTreeOutlined";
import DeleteIcon from "@mui/icons-material/Delete";
import AddIcon from "@mui/icons-material/Add";
import CheckIcon from "@mui/icons-material/Check";
import CloseIcon from "@mui/icons-material/Close";
import WarningAmberOutlinedIcon from "@mui/icons-material/WarningAmberOutlined";
import SecurityOutlinedIcon from "@mui/icons-material/SecurityOutlined";
import AssignmentOutlinedIcon from "@mui/icons-material/AssignmentOutlined";
import {
  addWorkflowRule,
  deleteAdminUser,
  deleteWorkflowRule,
  getAdminUsers,
  getPermissionsMatrix,
  getWorkflowConfig,
  inviteAdminUser,
  updatePermissionsMatrix,
  updateUserRole,
  updateWorkflowConfig,
} from "../api/auth";
import type { FolderPermissionItem, User, WorkflowConfig, WorkflowRule } from "../api/auth";
import { useAuth } from "../context/AuthContext";

type Tab = "users" | "permissions" | "workflow";
type RoleValue = "ADMIN" | "UPLOAD_MAKER" | "UPLOAD_CHECKER";

const roles: { value: RoleValue; label: string }[] = [
  { value: "ADMIN", label: "Admin" },
  { value: "UPLOAD_MAKER", label: "Upload Maker" },
  { value: "UPLOAD_CHECKER", label: "Upload Checker" },
];

function roleLabel(role: string) {
  return roles.find((item) => item.value === role.toUpperCase())?.label ?? "Upload Maker";
}

function getErrorMessage(error: unknown, fallback: string) {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}

function initials(name: string) {
  return name
    .trim()
    .split(/\s+/)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase() || "U";
}

export default function Settings() {
  const { user: currentUser, isAdmin, refreshUser } = useAuth();
  const [activeTab, setActiveTab] = useState<Tab>("users");
  const [users, setUsers] = useState<User[]>([]);
  const [permissions, setPermissions] = useState<FolderPermissionItem[]>([]);
  const [workflow, setWorkflow] = useState<WorkflowConfig | null>(null);
  const [draftThreshold, setDraftThreshold] = useState(80);
  const [draftRules, setDraftRules] = useState<WorkflowRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteName, setInviteName] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<RoleValue>("UPLOAD_MAKER");

  useEffect(() => {
    let mounted = true;
    Promise.all([getAdminUsers(), getPermissionsMatrix(), getWorkflowConfig()])
      .then(([userList, permissionMatrix, workflowConfig]) => {
        if (!mounted) return;
        setUsers(userList);
        setPermissions(permissionMatrix);
        setWorkflow(workflowConfig);
        setDraftThreshold(workflowConfig.threshold);
        setDraftRules(workflowConfig.rules);
      })
      .catch((loadError: unknown) => {
        if (mounted) setError(getErrorMessage(loadError, "Unable to load settings."));
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, []);

  const clearFeedback = () => {
    setError(null);
    setSuccess(null);
  };

  const handleInvite = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    clearFeedback();
    setWorking(true);
    try {
      const created = await inviteAdminUser({
        name: inviteName.trim(),
        email: inviteEmail.trim(),
        role: inviteRole,
      });
      setUsers((previous) => [...previous, created].sort((left, right) => left.id - right.id));
      setInviteName("");
      setInviteEmail("");
      setInviteRole("UPLOAD_MAKER");
      setInviteOpen(false);
      setSuccess("Member invited. They can sign in with the invited Google account.");
    } catch (inviteError: unknown) {
      setError(getErrorMessage(inviteError, "Unable to invite this member."));
    } finally {
      setWorking(false);
    }
  };

  const handleRoleChange = async (member: User, role: RoleValue) => {
    clearFeedback();
    setWorking(true);
    try {
      const updated = await updateUserRole(member.id, role);
      setUsers((previous) => previous.map((item) => item.id === updated.id ? updated : item));
      if (member.id === currentUser?.id) await refreshUser();
      setSuccess(`Role updated to ${roleLabel(updated.role)}.`);
    } catch (updateError: unknown) {
      setError(getErrorMessage(updateError, "Unable to update this member's role."));
    } finally {
      setWorking(false);
    }
  };

  const handleRemove = async (member: User) => {
    if (!window.confirm(`Deactivate ${member.name} (${member.email})? They will no longer be able to sign in.`)) return;
    clearFeedback();
    setWorking(true);
    try {
      await deleteAdminUser(member.id);
      setUsers((previous) => previous.map((item) => item.id === member.id ? { ...item, is_active: false } : item));
      setSuccess(`${member.name} has been deactivated.`);
    } catch (removeError: unknown) {
      setError(getErrorMessage(removeError, "Unable to deactivate this member."));
    } finally {
      setWorking(false);
    }
  };

  const handlePermissionChange = (folder: string, role: "upload_maker" | "upload_checker", allowed: boolean) => {
    setPermissions((previous) => previous.map((item) => item.folder === folder ? { ...item, [role]: allowed } : item));
    clearFeedback();
  };

  const handleSavePermissions = async () => {
    clearFeedback();
    setWorking(true);
    try {
      const updated = await updatePermissionsMatrix(permissions);
      setPermissions(updated);
      setSuccess("Folder permissions saved.");
    } catch (permissionError: unknown) {
      setError(getErrorMessage(permissionError, "Unable to save folder permissions."));
    } finally {
      setWorking(false);
    }
  };

  const updateRule = (index: number, changes: Partial<WorkflowRule>) => {
    setDraftRules((previous) => previous.map((rule, currentIndex) => currentIndex === index ? { ...rule, ...changes } : rule));
    clearFeedback();
  };

  const handleAddRule = async () => {
    clearFeedback();
    setWorking(true);
    try {
      const created = await addWorkflowRule({
        title: "New routing rule",
        route: "Review team",
        type: "warning",
        threshold: draftThreshold,
        document_type: "",
      });
      setDraftRules((previous) => [...previous, created]);
      setWorkflow((previous) => previous ? { ...previous, rules: [...previous.rules, created] } : previous);
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
      setWorkflow((previous) => previous ? { ...previous, rules: remaining } : previous);
      setSuccess("Routing rule removed.");
    } catch (ruleError: unknown) {
      setError(getErrorMessage(ruleError, "Unable to remove this routing rule."));
    } finally {
      setWorking(false);
    }
  };

  const handleSaveWorkflow = async () => {
    clearFeedback();
    setWorking(true);
    try {
      const updated = await updateWorkflowConfig({
        threshold: draftThreshold,
        rules: draftRules.map((rule) => ({
          ...rule,
          document_type: rule.document_type?.trim() || null,
        })),
      });
      setWorkflow(updated);
      setDraftThreshold(updated.threshold);
      setDraftRules(updated.rules);
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
    setDraftRules(workflow.rules);
    clearFeedback();
  };

  if (!isAdmin) {
    return <div className="settingsAccessDenied" role="alert">Access denied. Administrator privileges are required.</div>;
  }

  return (
    <div className="settingsPage">
      <div className="settingsHeader">
        <h1>Settings</h1>
        <p>Manage your team, permissions, and automation rules</p>
      </div>

      <div className="settingsTabs" role="tablist" aria-label="Administration settings">
        <button className={activeTab === "users" ? "settingsTab active" : "settingsTab"} onClick={() => setActiveTab("users")} role="tab" aria-selected={activeTab === "users"}>
          <GroupIcon /> Users &amp; roles
        </button>
        <button className={activeTab === "permissions" ? "settingsTab active" : "settingsTab"} onClick={() => setActiveTab("permissions")} role="tab" aria-selected={activeTab === "permissions"}>
          <ShieldOutlinedIcon /> Permissions
        </button>
        <button className={activeTab === "workflow" ? "settingsTab active" : "settingsTab"} onClick={() => setActiveTab("workflow")} role="tab" aria-selected={activeTab === "workflow"}>
          <AccountTreeOutlinedIcon /> Workflow rules
        </button>
      </div>

      {error && <div className="settingsNotice error" role="alert">{error}<button type="button" onClick={() => setError(null)} aria-label="Dismiss error">×</button></div>}
      {success && <div className="settingsNotice success" role="status">{success}<button type="button" onClick={() => setSuccess(null)} aria-label="Dismiss message">×</button></div>}
      {loading ? (
        <div className="settingsLoading" role="status">Loading settings…</div>
      ) : (
        <>
          {activeTab === "users" && (
            <section className="settingsSection" aria-label="Team members">
              <div className="sectionTop">
                <h2>Team members</h2>
                <button className="inviteButton" type="button" onClick={() => { clearFeedback(); setInviteOpen(true); }} disabled={working}>
                  <AddIcon /> Invite member
                </button>
              </div>
              <div className="settingsTable">
                <div className="settingsTableHeader"><span>Member</span><span>Role</span><span>Actions</span></div>
                {users.length === 0 ? <div className="settingsEmpty">No members found.</div> : users.map((member) => (
                  <div className="memberRow" key={member.id}>
                    <div className="memberInfo">
                      <MemberAvatar user={member} />
                      <div><strong>{member.name}</strong><span>{member.email}</span>{!member.is_active && <span className="memberStatus">Deactivated</span>}</div>
                    </div>
                    <select className="roleSelect" value={member.role.toUpperCase()} disabled={working || !member.is_active} aria-label={`Role for ${member.name}`} onChange={(event) => handleRoleChange(member, event.target.value as RoleValue)}>
                      {roles.map((role) => <option key={role.value} value={role.value}>{role.label}</option>)}
                    </select>
                    <button className="deleteMember" type="button" title={member.id === currentUser?.id ? "You cannot deactivate your own account" : "Deactivate member"} aria-label={`Deactivate ${member.name}`} disabled={working || !member.is_active || member.id === currentUser?.id} onClick={() => handleRemove(member)}>
                      <DeleteIcon />
                    </button>
                  </div>
                ))}
              </div>
            </section>
          )}

          {activeTab === "permissions" && (
            <section className="settingsSection" aria-label="Folder access permissions">
              <div className="sectionTop">
                <h2 className="sectionTitle">Folder access matrix</h2>
                <button type="button" className="saveButton" onClick={handleSavePermissions} disabled={working}><CheckIcon /> Save permissions</button>
              </div>
              {permissions.length === 0 ? <div className="settingsEmpty">No folder permissions are configured.</div> : (
                <div className="permissionTable">
                  <div className="permissionHeader"><span>Folder</span><span>Admin</span><span>Upload Maker</span><span>Upload Checker</span></div>
                  {permissions.map((item) => (
                    <div className="permissionRow" key={item.folder}>
                      <span className="folderName">{item.folder}</span>
                      <PermissionIcon allowed={item.admin} />
                      <label className="permissionToggle"><input type="checkbox" checked={item.upload_maker} disabled={working} aria-label={`${item.upload_maker ? "Allow" : "Deny"} Upload Maker access to ${item.folder}`} onChange={(event) => handlePermissionChange(item.folder, "upload_maker", event.target.checked)} /></label>
                      <label className="permissionToggle"><input type="checkbox" checked={item.upload_checker} disabled={working} aria-label={`${item.upload_checker ? "Allow" : "Deny"} Upload Checker access to ${item.folder}`} onChange={(event) => handlePermissionChange(item.folder, "upload_checker", event.target.checked)} /></label>
                    </div>
                  ))}
                </div>
              )}
            </section>
          )}

          {activeTab === "workflow" && (
            <section className="settingsSection" aria-label="Document workflow configuration">
              <h2 className="sectionTitle">Default confidence threshold</h2>
              <div className="thresholdCard">
                <div className="thresholdTop">
                  <div><h3>Auto-flag threshold</h3><p>Documents below this confidence are sent to the review queue</p></div>
                  <strong>{draftThreshold}%</strong>
                </div>
                <input type="range" min="50" max="100" value={draftThreshold} disabled={working} onChange={(event) => { setDraftThreshold(Number(event.target.value)); clearFeedback(); }} className="confidenceSlider" aria-label="Auto-flag confidence threshold" />
                <div className="sliderLabels"><span>50%</span><span>75%</span><span>100%</span></div>
              </div>
              <h2 className="sectionTitle routingTitle">Routing rules</h2>
              <div className="routingRules">
                {draftRules.map((rule, index) => (
                  <div className="routingRule" key={rule.id ?? `new-${index}`}>
                    <div className="routingIcon">{rule.type === "security" ? <SecurityOutlinedIcon /> : rule.type === "claims" ? <AssignmentOutlinedIcon /> : <WarningAmberOutlinedIcon />}</div>
                    <div className="routingInfo">
                      <label>Rule title<input value={rule.title} disabled={working} onChange={(event) => updateRule(index, { title: event.target.value })} /></label>
                      <label>Document type<input value={rule.document_type ?? ""} disabled={working} onChange={(event) => updateRule(index, { document_type: event.target.value })} placeholder="Any document type" /></label>
                      <label>Route to<input value={rule.route} disabled={working} onChange={(event) => updateRule(index, { route: event.target.value })} /></label>
                      <div className="workflowRuleOptions">
                        <label>Rule type<select value={rule.type} disabled={working} onChange={(event) => updateRule(index, { type: event.target.value })}><option value="warning">Warning</option><option value="security">Security</option><option value="claims">Claims</option></select></label>
                        <label>Threshold<input type="number" min="50" max="100" value={rule.threshold ?? draftThreshold} disabled={working} onChange={(event) => updateRule(index, { threshold: Number(event.target.value) })} /></label>
                      </div>
                    </div>
                    <button className="deleteMember" type="button" title="Remove routing rule" aria-label={`Remove ${rule.title}`} disabled={working} onClick={() => handleDeleteRule(rule, index)}><DeleteIcon /></button>
                  </div>
                ))}
                <button className="addRuleButton" type="button" disabled={working} onClick={handleAddRule}><AddIcon /> Add routing rule</button>
              </div>
              <div className="workflowActions">
                <button className="discardButton" type="button" disabled={working || !workflow} onClick={discardWorkflowChanges}>Discard changes</button>
                <button className="saveButton" type="button" disabled={working} onClick={handleSaveWorkflow}>{working ? "Saving…" : "Save changes"}</button>
              </div>
            </section>
          )}
        </>
      )}

      {inviteOpen && (
        <div className="settingsModalOverlay" role="presentation">
          <form className="settingsModal" onSubmit={handleInvite} role="dialog" aria-modal="true" aria-labelledby="inviteTitle">
            <h2 id="inviteTitle">Invite member</h2>
            <p>New members can sign in with the Google account matching this email.</p>
            <label>Full name<input autoFocus required maxLength={150} value={inviteName} onChange={(event) => setInviteName(event.target.value)} /></label>
            <label>Email address<input required type="email" maxLength={255} value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} /></label>
            <label>Role<select value={inviteRole} onChange={(event) => setInviteRole(event.target.value as RoleValue)}>{roles.map((role) => <option key={role.value} value={role.value}>{role.label}</option>)}</select></label>
            <div className="settingsModalActions">
              <button className="discardButton" type="button" disabled={working} onClick={() => setInviteOpen(false)}>Cancel</button>
              <button className="saveButton" type="submit" disabled={working}>{working ? "Inviting…" : "Invite member"}</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );

}

function PermissionIcon({ allowed }: { allowed: boolean }) {
  return <div className={allowed ? "permissionIcon allowed" : "permissionIcon denied"} aria-label={allowed ? "Allowed" : "Denied"}>
    {allowed ? <CheckIcon /> : <CloseIcon />}
  </div>;
}

function MemberAvatar({ user }: { user: User }) {
  const [imageFailed, setImageFailed] = useState(false);
  if (user.profile_picture && !imageFailed) {
    return <img className="memberAvatar" src={user.profile_picture} alt="" onError={() => setImageFailed(true)} />;
  }
  return <div className="memberAvatar">{initials(user.name)}</div>;
}
