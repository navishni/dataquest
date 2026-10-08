/**
 * Agent 09 - Chart / Figure.
 * Purpose: crops a chart or figure region and, for charts, returns grounded series and an insight.
 * Input:   { source_id, page_number, region_id }
 * Output:  ChartBlock | FigureBlock
 * Endpoint: POST /agents/chart-figure
 */
import { z } from "zod";
import { apiRequest } from "@/api/client";
import { chartBlockSchema, figureBlockSchema, type Schema } from "@/types/schemas";
import type { ChartBlock, FigureBlock, ID } from "@/types/canonical";

export const ENDPOINT = "/agents/chart-figure";
export interface Input { source_id: ID; page_number: number; region_id: ID }
export type Output = ChartBlock | FigureBlock;
export const outputSchema: Schema<Output> = z.union([chartBlockSchema, figureBlockSchema]);
export const extractChartFigure = (input: Input) => apiRequest<Output>(ENDPOINT, { method: "POST", json: input, schema: outputSchema });
