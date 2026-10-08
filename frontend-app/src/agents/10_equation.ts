/**
 * Agent 10 - Equation.
 * Purpose: recognises an equation region and reports whether the result was verified.
 * Input:   { source_id, page_number, region_id }
 * Output:  EquationBlock
 * Endpoint: POST /agents/equation
 */
import { apiRequest } from "@/api/client";
import { equationBlockSchema } from "@/types/schemas";
import type { EquationBlock, ID } from "@/types/canonical";

export const ENDPOINT = "/agents/equation";
export interface Input { source_id: ID; page_number: number; region_id: ID }
export type Output = EquationBlock;
export const outputSchema = equationBlockSchema;
export const extractEquation = (input: Input) => apiRequest<Output>(ENDPOINT, { method: "POST", json: input, schema: outputSchema });
