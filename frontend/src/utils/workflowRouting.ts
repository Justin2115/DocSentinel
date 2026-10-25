import type { WorkflowOptions, WorkflowRule } from "../api/auth";

export const WORKFLOW_CONDITION_CONFIDENCE_BELOW = "confidence_below_threshold";

export function optionsForDepartment(options: WorkflowOptions | null, department: string | null | undefined) {
	const dept = (department || "").trim().toUpperCase();
	const documentTypes = (options?.document_types || []).filter(
		(item) => !dept || (item.department || "").toUpperCase() === dept
	);
	const routes = (options?.routes || []).filter(
		(item) => !dept || (item.department || "").toUpperCase() === dept
	);
	return {
		departments: options?.departments || [],
		documentTypes,
		routes,
		ruleTypes: options?.rule_types || [],
		conditions: options?.conditions || [],
	};
}

export function ruleTitle(documentType: string, threshold: number) {
	const typeLabel = documentType.trim() || "Document";
	return `${typeLabel} below ${threshold}% confidence`;
}

export function validateWorkflowRule(
	rule: WorkflowRule,
	options: WorkflowOptions | null,
	canChooseDepartment: boolean
): string | null {
	const department = (rule.department || "").trim();
	if (canChooseDepartment && !department) {
		return "Select a department for every routing rule.";
	}
	const scoped = optionsForDepartment(options, department || rule.department);
	if (!rule.document_type?.trim()) {
		return "Select a document type for every routing rule.";
	}
	if (!scoped.documentTypes.some((item) => item.value === rule.document_type)) {
		return "Document type is not valid for the selected department.";
	}
	if (!rule.route?.trim()) {
		return "Select a Route to destination for every routing rule.";
	}
	if (!scoped.routes.some((item) => item.value === rule.route)) {
		return "Route to must be an eligible team or user for the selected department.";
	}
	if (!rule.type?.trim() || !scoped.ruleTypes.some((item) => item.value === rule.type)) {
		return "Select a valid rule type.";
	}
	const condition = rule.condition || WORKFLOW_CONDITION_CONFIDENCE_BELOW;
	if (!scoped.conditions.some((item) => item.value === condition)) {
		return "Select a valid workflow condition.";
	}
	const threshold = Number(rule.threshold);
	if (!Number.isFinite(threshold) || threshold < 50 || threshold > 100) {
		return "Threshold must be between 50 and 100.";
	}
	return null;
}

export function validateWorkflowRules(
	rules: WorkflowRule[],
	options: WorkflowOptions | null,
	canChooseDepartment: boolean
): string | null {
	for (const rule of rules) {
		const error = validateWorkflowRule(rule, options, canChooseDepartment);
		if (error) return error;
	}
	return null;
}
