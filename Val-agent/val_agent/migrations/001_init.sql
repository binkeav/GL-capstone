PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS invoices (
  invoice_id TEXT PRIMARY KEY,
  invoice_number TEXT,
  invoice_date TEXT,
  due_date TEXT,
  po_number TEXT,
  payment_terms TEXT,
  vendor_name TEXT,
  vendor_tax_id TEXT,
  customer_name TEXT,
  customer_tax_id TEXT,
  subtotal NUMERIC,
  tax NUMERIC,
  shipping NUMERIC DEFAULT 0,
  discounts NUMERIC DEFAULT 0,
  total NUMERIC,
  currency TEXT,
  source_payload_json TEXT NOT NULL,
  normalized_payload_json TEXT,
  validation_state TEXT NOT NULL DEFAULT 'PENDING'
    CHECK (validation_state IN (
      'PENDING',
      'STRAIGHT_THROUGH_PROCESSING',
      'HUMAN_IN_THE_LOOP',
      'CRITICAL_REJECTION'
    )),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invoice_items (
  invoice_item_id TEXT PRIMARY KEY,
  invoice_id TEXT NOT NULL,
  line_no INTEGER,
  description TEXT,
  qty NUMERIC,
  unit TEXT,
  unit_price NUMERIC,
  net_amount NUMERIC,
  tax_rate NUMERIC,
  gross_amount NUMERIC,
  FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id)
);

CREATE TABLE IF NOT EXISTS conversations (
  conversation_id TEXT PRIMARY KEY,
  invoice_id TEXT,
  user_id TEXT,
  user_role TEXT,
  status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id)
);

CREATE TABLE IF NOT EXISTS chat_messages (
  message_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  invoice_id TEXT,
  sender TEXT NOT NULL CHECK (sender IN ('USER', 'ASSISTANT', 'SYSTEM', 'TOOL')),
  message_text TEXT,
  message_json TEXT,
  intent TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id),
  FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id)
);

CREATE TABLE IF NOT EXISTS vendors (
  vendor_id TEXT PRIMARY KEY,
  vendor_name TEXT NOT NULL,
  vendor_tax_id TEXT,
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE', 'BLOCKED')),
  default_currency TEXT,
  jurisdiction TEXT DEFAULT 'IN',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS purchase_orders (
  po_number TEXT PRIMARY KEY,
  vendor_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('OPEN', 'PARTIALLY_RECEIVED', 'CLOSED', 'CANCELLED')),
  currency TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (vendor_id) REFERENCES vendors(vendor_id)
);

CREATE TABLE IF NOT EXISTS purchase_order_items (
  po_item_id TEXT PRIMARY KEY,
  po_number TEXT NOT NULL,
  line_no INTEGER,
  description TEXT,
  ordered_qty NUMERIC,
  unit TEXT,
  unit_price NUMERIC,
  FOREIGN KEY (po_number) REFERENCES purchase_orders(po_number)
);

CREATE TABLE IF NOT EXISTS goods_receipts (
  receipt_id TEXT PRIMARY KEY,
  po_number TEXT NOT NULL,
  po_item_id TEXT,
  received_qty NUMERIC NOT NULL,
  received_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('RECEIVED', 'PARTIAL', 'REJECTED', 'REVERSED')),
  FOREIGN KEY (po_number) REFERENCES purchase_orders(po_number),
  FOREIGN KEY (po_item_id) REFERENCES purchase_order_items(po_item_id)
);

CREATE TABLE IF NOT EXISTS compliance_policies (
  policy_id TEXT PRIMARY KEY,
  jurisdiction TEXT NOT NULL DEFAULT 'IN',
  required_tax_id_type TEXT DEFAULT 'GSTIN',
  max_invoice_age_days INTEGER NOT NULL DEFAULT 90,
  supported_currency TEXT NOT NULL DEFAULT 'INR',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rule_results (
  rule_result_id TEXT PRIMARY KEY,
  invoice_id TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  agent_name TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('PASS', 'FAIL', 'SKIPPED')),
  severity TEXT NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'ERROR', 'FATAL')),
  expected_value TEXT,
  actual_value TEXT,
  error_code TEXT,
  evidence_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id)
);

CREATE TABLE IF NOT EXISTS agent_traces (
  trace_id TEXT PRIMARY KEY,
  invoice_id TEXT NOT NULL,
  langgraph_run_id TEXT,
  agent_name TEXT NOT NULL,
  node_name TEXT,
  input_json TEXT,
  output_json TEXT,
  tool_calls_json TEXT,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
  FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id)
);

CREATE TABLE IF NOT EXISTS override_events (
  override_id TEXT PRIMARY KEY,
  invoice_id TEXT NOT NULL,
  previous_state TEXT NOT NULL,
  new_state TEXT NOT NULL,
  failed_rule_ids_json TEXT NOT NULL,
  approver_role TEXT NOT NULL,
  approver_user_id TEXT NOT NULL,
  override_reason TEXT NOT NULL,
  supporting_notes TEXT,
  revalidation_required INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id)
);

CREATE TABLE IF NOT EXISTS erp_submissions (
  submission_id TEXT PRIMARY KEY,
  invoice_id TEXT NOT NULL,
  submission_status TEXT NOT NULL CHECK (submission_status IN ('NOT_SUBMITTED', 'SUBMITTED', 'ACCEPTED', 'REJECTED')),
  erp_reference_id TEXT,
  submitted_at TEXT,
  response_json TEXT,
  FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id)
);

CREATE INDEX IF NOT EXISTS idx_invoices_vendor_invoice ON invoices(vendor_name, invoice_number);
CREATE INDEX IF NOT EXISTS idx_invoices_total_date ON invoices(total, invoice_date);
CREATE INDEX IF NOT EXISTS idx_invoice_items_invoice_id ON invoice_items(invoice_id);
CREATE INDEX IF NOT EXISTS idx_conversations_invoice_id ON conversations(invoice_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation_id ON chat_messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_rule_results_invoice_id ON rule_results(invoice_id);
CREATE INDEX IF NOT EXISTS idx_agent_traces_invoice_id ON agent_traces(invoice_id);
CREATE INDEX IF NOT EXISTS idx_override_events_invoice_id ON override_events(invoice_id);

