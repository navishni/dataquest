// Re-exports every agent client as a namespace plus a registry used by the Agent Status contract check.
export * as a01 from "./01_fileValidation";
export * as a02 from "./02_formatRouter";
export * as a03 from "./03_nativeText";
export * as a04 from "./04_ocr";
export * as a05 from "./05_layoutDetection";
export * as a06 from "./06_readingOrder";
export * as a07 from "./07_tableExtraction";
export * as a08 from "./08_spreadsheet";
export * as a09 from "./09_chartFigure";
export * as a10 from "./10_equation";
export * as a11 from "./11_jsonAssembly";
export * as a12 from "./12_confidenceValidation";
export * as a13 from "./13_virtualMerge";
export * as a14 from "./14_caseLinker";
export * as a15 from "./15_factNormalizer";
export * as a16 from "./16_crossDocReasoning";
export * as a17 from "./17_actionDraft";
export * as a18 from "./18_humanApproval";
export * as a19 from "./19_audit";
export * as a20 from "./20_export";
export * as a21 from "./21_consensus";
export * as a22 from "./22_urlGuardWebRender";
export * as a23 from "./23_accessControl";
export * as a24 from "./24_chatSql";

import * as e01 from "./01_fileValidation";
import * as e02 from "./02_formatRouter";
import * as e03 from "./03_nativeText";
import * as e04 from "./04_ocr";
import * as e05 from "./05_layoutDetection";
import * as e06 from "./06_readingOrder";
import * as e07 from "./07_tableExtraction";
import * as e08 from "./08_spreadsheet";
import * as e09 from "./09_chartFigure";
import * as e10 from "./10_equation";
import * as e11 from "./11_jsonAssembly";
import * as e12 from "./12_confidenceValidation";
import * as e13 from "./13_virtualMerge";
import * as e14 from "./14_caseLinker";
import * as e15 from "./15_factNormalizer";
import * as e16 from "./16_crossDocReasoning";
import * as e17 from "./17_actionDraft";
import * as e18 from "./18_humanApproval";
import * as e19 from "./19_audit";
import * as e20 from "./20_export";
import * as e21 from "./21_consensus";
import * as e22 from "./22_urlGuardWebRender";
import * as e23 from "./23_accessControl";
import * as e24 from "./24_chatSql";

export interface AgentEndpoint { agent: string; method: "GET" | "POST"; path: string }

/** Every endpoint the frontend calls, for the Agent Status contract check. Names are file names, not domain data. */
export const AGENT_ENDPOINTS: AgentEndpoint[] = [
  { agent: "01_fileValidation", method: "POST", path: e01.ENDPOINT },
  { agent: "02_formatRouter", method: "POST", path: e02.ENDPOINT },
  { agent: "03_nativeText", method: "POST", path: e03.ENDPOINT },
  { agent: "04_ocr", method: "POST", path: e04.ENDPOINT },
  { agent: "05_layoutDetection", method: "POST", path: e05.ENDPOINT },
  { agent: "06_readingOrder", method: "POST", path: e06.ENDPOINT },
  { agent: "07_tableExtraction", method: "POST", path: e07.ENDPOINT },
  { agent: "08_spreadsheet", method: "POST", path: e08.ENDPOINT },
  { agent: "09_chartFigure", method: "POST", path: e09.ENDPOINT },
  { agent: "10_equation", method: "POST", path: e10.ENDPOINT },
  { agent: "11_jsonAssembly", method: "POST", path: e11.ENDPOINT },
  { agent: "12_confidenceValidation", method: "POST", path: e12.ENDPOINT },
  { agent: "13_virtualMerge", method: "POST", path: e13.ENDPOINT },
  { agent: "14_caseLinker", method: "POST", path: e14.ENDPOINT },
  { agent: "14_caseLinker (decision)", method: "POST", path: e14.ENDPOINT_DECISION },
  { agent: "15_factNormalizer", method: "POST", path: e15.ENDPOINT },
  { agent: "16_crossDocReasoning", method: "POST", path: e16.ENDPOINT },
  { agent: "17_actionDraft", method: "POST", path: e17.ENDPOINT },
  { agent: "18_humanApproval", method: "POST", path: e18.ENDPOINT },
  { agent: "19_audit", method: "GET", path: e19.ENDPOINT },
  { agent: "20_export", method: "POST", path: e20.ENDPOINT },
  { agent: "20_export (history)", method: "GET", path: e20.ENDPOINT_HISTORY },
  { agent: "21_consensus", method: "POST", path: e21.ENDPOINT },
  { agent: "22_urlGuardWebRender", method: "POST", path: e22.ENDPOINT },
  { agent: "23_accessControl (schema)", method: "GET", path: e23.ENDPOINT_SCHEMA },
  { agent: "23_accessControl (preview)", method: "GET", path: e23.ENDPOINT_PREVIEW },
  { agent: "23_accessControl (request)", method: "POST", path: e23.ENDPOINT_REQUEST },
  { agent: "23_accessControl (requests)", method: "GET", path: e23.ENDPOINT_REQUESTS },
  { agent: "23_accessControl (decision)", method: "POST", path: e23.ENDPOINT_DECISION },
  { agent: "24_chatSql", method: "POST", path: e24.ENDPOINT },
];
