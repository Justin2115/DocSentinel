import assert from "node:assert/strict";
import { describe, it } from "node:test";

import type { WorkflowOptions, WorkflowRule } from "../api/auth";
import {
	optionsForDepartment,
	ruleTitle,
	validateWorkflowRule,
} from "./workflowRouting.ts";

const options: WorkflowOptions = {
	departments: [
		{ value: "FINANCE", label: "Finance" },
		{ value: "HR", label: "HR" },
	],
	document_types: [
		{ value: "Invoices", label: "Invoices", department: "FINANCE" },
		{ value: "Appraisals", label: "Appraisals", department: "HR" },
	],
	routes: [
		{ value: "Finance Admin", label: "Finance Admin (Department Admin)", department: "FINANCE", kind: "user" },
		{ value: "HR Admin", label: "HR Admin (Department Admin)", department: "HR", kind: "user" },
	],
	rule_types: [{ value: "warning", label: "Warning" }],
	conditions: [{ value: "confidence_below_threshold", label: "Confidence below threshold" }],
	priority_supported: false,
	can_edit_threshold: true,
};

describe("workflowRouting", () => {
	it("filters document types and routes by department", () => {
		const scoped = optionsForDepartment(options, "FINANCE");
		assert.deepEqual(
			scoped.documentTypes.map((item) => item.value),
			["Invoices"]
		);
		assert.deepEqual(
			scoped.routes.map((item) => item.value),
			["Finance Admin"]
		);
	});

	it("rejects a rule whose document type belongs to another department", () => {
		const rule: WorkflowRule = {
			title: "Cross department",
			route: "Finance Admin",
			type: "warning",
			document_type: "Appraisals",
			department: "FINANCE",
			threshold: 80,
			condition: "confidence_below_threshold",
		};
		assert.equal(
			validateWorkflowRule(rule, options, true),
			"Document type is not valid for the selected department."
		);
	});

	it("accepts a complete in-department rule and builds a title", () => {
		const rule: WorkflowRule = {
			title: ruleTitle("Invoices", 82),
			route: "Finance Admin",
			type: "warning",
			document_type: "Invoices",
			department: "FINANCE",
			threshold: 82,
			condition: "confidence_below_threshold",
		};
		assert.equal(validateWorkflowRule(rule, options, true), null);
		assert.equal(rule.title, "Invoices below 82% confidence");
	});
});
