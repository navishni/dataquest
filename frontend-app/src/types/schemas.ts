// Runtime validation (zod) for canonical shapes. Objects are passthrough so unknown fields/block types never crash the UI.
import { z } from "zod";
import type {
  Alternative, ApiError, Block, BoundingBox, EvidenceReference, PageUnit, SourceDocument, TableBlock, TableCell,
  ValidationCheck, WarningItem, ChartBlock, EquationBlock, FigureBlock,
} from "./canonical";

export type Schema<T> = z.ZodType<T, z.ZodTypeDef, unknown>;

export const apiErrorSchema: Schema<ApiError> = z
  .object({ code: z.string(), message: z.string(), details: z.record(z.unknown()).optional() })
  .passthrough();

export const warningSchema: Schema<WarningItem> = z
  .object({ code: z.string(), message: z.string(),
    block_id: z.string().nullish().transform((v) => v ?? undefined),
    source_id: z.string().nullish().transform((v) => v ?? undefined) })
  .passthrough();

const bboxTuple = z.tuple([z.number(), z.number(), z.number(), z.number()]);

export const boundingBoxSchema: Schema<BoundingBox> = z
  .object({
    bbox: bboxTuple.nullable(),
    coordinate_system: z.literal("pixel_top_left"),
    page_width: z.number(),
    page_height: z.number(),
    bbox_unavailable_reason: z.string().nullish().transform((v) => v ?? undefined),
  })
  .passthrough();

export const alternativeSchema: Schema<Alternative> = z
  .object({ extractor: z.string(), value: z.string(), confidence: z.number() })
  .passthrough();

export const validationCheckSchema: Schema<ValidationCheck> = z
  .object({ name: z.string(), status: z.string(), detail: z.string().nullish().transform((v) => v ?? undefined) })
  .passthrough();

/** zod infers `unknown` object members as optional; this narrows the schema to the documented (required) shape. */
export function asSchema<T>(s: z.ZodTypeAny): Schema<T> {
  return s as unknown as Schema<T>;
}

const normalizedSchema = asSchema<{ value: unknown; rule: string; currency?: string; unit?: string }>(
  z.object({ value: z.unknown(), rule: z.string(), currency: z.string().optional(), unit: z.string().optional() }).passthrough(),
);

const nn = <T extends z.ZodTypeAny>(s: T) => s.nullish().transform((v) => v ?? undefined);

const baseShape = {
  block_id: z.string(),
  type: z.string(),
  source_id: z.string(),
  page_id: z.string(),
  unit_id: nn(z.string()),
  virtual_page_number: nn(z.number()),
  reading_order_index: z.number(),
  location: boundingBoxSchema,
  confidence: z.number(),
  confidence_breakdown: nn(z.record(z.number())),
  extraction_method: z.string(),
  raw_text: nn(z.string()),
  raw_value: nn(z.string()),
  normalized: nn(normalizedSchema),
  needs_review: z.boolean(),
  warnings: z.array(warningSchema),
  alternatives: nn(z.array(alternativeSchema)),
  validation: nn(z.array(validationCheckSchema)),
  locked: nn(z.boolean()),
  masked: nn(z.boolean()),
};

export const baseBlockSchema = z.object(baseShape).passthrough();
export const blockSchema: Schema<Block> = baseBlockSchema;

export const tableCellSchema: Schema<TableCell> = z
  .object({
    row: z.number(), col: z.number(), row_span: z.number(), col_span: z.number(), is_header: z.boolean(),
    raw_text: z.string(), normalized: nn(normalizedSchema), location: boundingBoxSchema, confidence: z.number(),
    alternatives: nn(z.array(alternativeSchema)), locked: nn(z.boolean()),
  })
  .passthrough();

export const tableBlockSchema: Schema<TableBlock> = z
  .object({
    ...baseShape,
    type: z.literal("table"),
    table_id: z.string(),
    caption: nn(z.string()),
    n_rows: z.number(),
    n_cols: z.number(),
    cells: z.array(tableCellSchema),
    continues_from: nn(z.string()),
    continues_to: nn(z.string()),
    badges: nn(z.array(z.object({ label: z.string(), status: z.string(), detail: nn(z.string()) }).passthrough())),
  })
  .passthrough();

const chartShape = {
  ...baseShape,
  type: z.literal("chart"),
  chart_id: z.string(),
  chart_type: nn(z.string()),
  title: nn(z.string()),
  crop_url: z.string(),
  axes: z.unknown().optional(),
  series: nn(z.array(z.object({
    name: z.string(),
    points: z.array(z.object({ x: z.union([z.string(), z.number()]), y: z.number() }).passthrough()),
  }).passthrough())),
  insight_text: nn(z.string()),
};
export const chartBlockSchema: Schema<ChartBlock> = z.object(chartShape).passthrough();

export const equationBlockSchema: Schema<EquationBlock> = z
  .object({
    ...baseShape, type: z.literal("equation"), equation_id: z.string(), latex: nn(z.string()),
    plain_text: nn(z.string()), crop_url: z.string(), verified: z.boolean(),
  })
  .passthrough();

export const figureBlockSchema: Schema<FigureBlock> = z
  .object({ ...baseShape, type: z.literal("figure"), figure_id: z.string(), crop_url: z.string(), caption: nn(z.string()) })
  .passthrough();

export const pageUnitSchema: Schema<PageUnit> = z
  .object({
    page_id: z.string(), source_id: z.string(), unit_id: z.string(), page_number: z.number(), width: z.number(),
    height: z.number(), rotation: z.number(), layout_class: z.string(), reading_order_confidence: z.number(),
    coverage_score: nn(z.number()), image_url: z.string(), uncovered_regions: nn(z.array(boundingBoxSchema)),
    blocks: z.array(blockSchema),
  })
  .passthrough();

export const sourceDocumentSchema: Schema<SourceDocument> = z
  .object({
    source_id: z.string(), filename: z.string(), kind: z.string(), status: z.string(), sha256: z.string(),
    size_bytes: z.number(), page_count: z.number(),
    origin: z.object({
      type: z.enum(["upload", "url", "email_attachment"]), url: nn(z.string()), parent_source_id: nn(z.string()),
    }).passthrough(),
    document_confidence: nn(z.number()),
    warnings: z.array(warningSchema),
    errors: z.array(apiErrorSchema),
    pages: nn(z.array(pageUnitSchema)),
  })
  .passthrough();

export const evidenceReferenceSchema: Schema<EvidenceReference> = z
  .object({
    source_id: z.string(), filename: z.string(), page_number: z.number(), block_id: z.string(),
    text_excerpt: z.string(), bbox: bboxTuple.nullable(), confidence: z.number(),
  })
  .passthrough();

export { nn };
