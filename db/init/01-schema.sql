-- CRM-lite schema for the sales follow-up demo.
-- Plain Postgres. The same SQL runs on Supabase (it is Postgres too).

CREATE TABLE accounts (
    id                serial PRIMARY KEY,
    company           text        NOT NULL,
    contact_name      text        NOT NULL,
    email             text        NOT NULL,
    kind              text        NOT NULL CHECK (kind IN ('customer', 'prospect')),
    segment           text        NOT NULL,
    city              text,
    state             text,
    sales_rep         text        NOT NULL,
    source            text,                       -- where a prospect came from
    notes             text,
    do_not_contact    boolean     NOT NULL DEFAULT false,
    last_contacted_at timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE orders (
    id           serial PRIMARY KEY,
    account_id   int           NOT NULL REFERENCES accounts(id),
    order_date   date          NOT NULL,
    amount       numeric(10,2) NOT NULL,
    main_product text          NOT NULL
);
CREATE INDEX orders_account_date ON orders (account_id, order_date DESC);

-- One row per follow-up email. The UNIQUE constraint is the idempotency guard:
-- an account gets at most one follow-up per cycle_key.
--   reconnect: cycle_key = 'quiet-since-<last order date>'  (one per quiet period)
--   intro:     cycle_key = 'intro'                          (one first-touch email, ever)
CREATE TABLE followups (
    id             serial PRIMARY KEY,
    account_id     int         NOT NULL REFERENCES accounts(id),
    kind           text        NOT NULL CHECK (kind IN ('reconnect', 'intro')),
    cycle_key      text        NOT NULL,
    status         text        NOT NULL DEFAULT 'drafting' CHECK (status IN (
                       'drafting',          -- reserved by the daily run, draft being written
                       'draft_failed',      -- the AI/template step failed; retried next run
                       'pending_approval',  -- waiting for a person
                       'sending',           -- approved, send in progress
                       'sent',
                       'send_failed',
                       'rejected')),
    subject        text,
    body           text,
    drafted_by     text,
    approval_token uuid        NOT NULL DEFAULT gen_random_uuid(),
    error          text,
    decided_by     text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    decided_at     timestamptz,
    sent_at        timestamptz,
    UNIQUE (account_id, cycle_key)
);

-- Everything that happens to a follow-up, in order. This is the "CRM-lite" history.
CREATE TABLE activity_log (
    id          serial PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    account_id  int REFERENCES accounts(id),
    followup_id int REFERENCES followups(id),
    event       text NOT NULL,
    detail      text
);

-- ---------------------------------------------------------------------------
-- Daily run: pick who needs an email and reserve a follow-up row for each.
-- Returns only rows reserved by THIS call, so running it twice in a day
-- returns nothing the second time.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION reserve_followups(p_inactive_days int, p_prospect_limit int)
RETURNS TABLE (
    followup_id     int,
    approval_token  uuid,
    kind            text,
    account_id      int,
    company         text,
    contact_name    text,
    email           text,
    segment         text,
    city            text,
    sales_rep       text,
    notes           text,
    last_order_date date,
    days_quiet      int,
    last_product    text,
    fit_reason      text
)
LANGUAGE plpgsql AS $fn$
#variable_conflict use_column
BEGIN
    RETURN QUERY
    WITH stats AS (
        SELECT o.account_id AS acc,
               max(o.order_date) AS last_order,
               sum(o.amount)     AS total,
               (array_agg(o.main_product ORDER BY o.order_date DESC))[1] AS last_prod
        FROM orders o
        GROUP BY o.account_id
    ),
    best_segments AS (      -- the two segments that buy the most
        SELECT a.segment
        FROM accounts a JOIN stats s ON s.acc = a.id
        WHERE a.kind = 'customer'
        GROUP BY a.segment
        ORDER BY sum(s.total) DESC
        LIMIT 2
    ),
    best_states AS (        -- the two states that buy the most
        SELECT a.state
        FROM accounts a JOIN stats s ON s.acc = a.id
        WHERE a.kind = 'customer'
        GROUP BY a.state
        ORDER BY sum(s.total) DESC
        LIMIT 2
    ),
    reconnect AS (
        SELECT a.id AS acc, 'reconnect'::text AS k, 'quiet-since-' || s.last_order::text AS ck,
               NULL::text AS why
        FROM accounts a JOIN stats s ON s.acc = a.id
        WHERE a.kind = 'customer'
          AND NOT a.do_not_contact
          AND s.last_order < current_date - p_inactive_days
    ),
    intro AS (
        SELECT a.id AS acc, 'intro'::text AS k, 'intro'::text AS ck,
               concat_ws(' and ',
                   CASE WHEN a.segment IN (SELECT bs.segment FROM best_segments bs)
                        THEN 'same segment as our best customers' END,
                   CASE WHEN a.state IN (SELECT bt.state FROM best_states bt)
                        THEN 'in a region we already deliver to' END) AS why
        FROM accounts a
        WHERE a.kind = 'prospect'
          AND NOT a.do_not_contact
          AND NOT EXISTS (SELECT 1 FROM followups f
                          WHERE f.account_id = a.id AND f.cycle_key = 'intro'
                            AND f.status <> 'draft_failed')
        ORDER BY (CASE WHEN a.segment IN (SELECT bs.segment FROM best_segments bs) THEN 2 ELSE 0 END
                + CASE WHEN a.state   IN (SELECT bt.state FROM best_states bt)   THEN 1 ELSE 0 END) DESC,
                 a.id
        -- p_prospect_limit is a DAILY cap: intros already drafted today count against it
        LIMIT greatest(p_prospect_limit - (SELECT count(*) FROM followups f2
                                           WHERE f2.kind = 'intro'
                                             AND f2.status <> 'draft_failed'
                                             AND f2.created_at >= current_date), 0)
    ),
    candidates AS (
        SELECT * FROM reconnect
        UNION ALL
        SELECT * FROM intro
    ),
    reserved AS (
        INSERT INTO followups AS f (account_id, kind, cycle_key)
        SELECT c.acc, c.k, c.ck FROM candidates c
        ON CONFLICT ON CONSTRAINT followups_account_id_cycle_key_key DO UPDATE
            SET status = 'drafting', error = NULL, updated_at = now()
            WHERE f.status = 'draft_failed'
               OR (f.status = 'drafting' AND f.updated_at < now() - interval '1 hour')
        RETURNING f.id, f.approval_token, f.kind, f.account_id
    )
    SELECT r.id, r.approval_token, r.kind, a.id, a.company, a.contact_name, a.email,
           a.segment, a.city, a.sales_rep, a.notes,
           s.last_order, (current_date - s.last_order)::int, s.last_prod,
           (SELECT c.why FROM candidates c WHERE c.acc = a.id LIMIT 1)
    FROM reserved r
    JOIN accounts a ON a.id = r.account_id
    LEFT JOIN stats s ON s.acc = a.id
    ORDER BY r.kind DESC, a.company;
END
$fn$;

CREATE OR REPLACE FUNCTION save_draft(p_id int, p_subject text, p_body text, p_drafted_by text)
RETURNS TABLE (followup_id int, status text)
LANGUAGE plpgsql AS $fn$
#variable_conflict use_column
BEGIN
    RETURN QUERY
    WITH upd AS (
        UPDATE followups f
           SET status = 'pending_approval', subject = p_subject, body = p_body,
               drafted_by = p_drafted_by, error = NULL, updated_at = now()
         WHERE f.id = p_id AND f.status = 'drafting'
        RETURNING f.id, f.account_id, f.status
    ), log AS (
        INSERT INTO activity_log (account_id, followup_id, event, detail)
        SELECT upd.account_id, upd.id, 'drafted', 'by ' || p_drafted_by FROM upd
    )
    SELECT upd.id, upd.status FROM upd;
END
$fn$;

CREATE OR REPLACE FUNCTION mark_draft_failed(p_id int, p_error text)
RETURNS TABLE (followup_id int, status text)
LANGUAGE plpgsql AS $fn$
#variable_conflict use_column
BEGIN
    RETURN QUERY
    WITH upd AS (
        UPDATE followups f SET status = 'draft_failed', error = p_error, updated_at = now()
         WHERE f.id = p_id AND f.status = 'drafting'
        RETURNING f.id, f.account_id, f.status
    ), log AS (
        INSERT INTO activity_log (account_id, followup_id, event, detail)
        SELECT upd.account_id, upd.id, 'draft_failed', p_error FROM upd
    )
    SELECT upd.id, upd.status FROM upd;
END
$fn$;

-- ---------------------------------------------------------------------------
-- Approval: a person approves (optionally after editing) or rejects a draft.
-- Only a row that is still 'pending_approval' AND has the matching token moves.
-- A second click on the same link returns no row, so nothing is sent twice.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION decide_followup(p_id int, p_token text, p_decision text,
                                           p_subject text, p_body text, p_by text)
RETURNS TABLE (followup_id int, decision text, account_id int, company text,
               contact_name text, email text, subject text, body text, sales_rep text)
LANGUAGE plpgsql AS $fn$
#variable_conflict use_column
DECLARE
    v_row     followups%ROWTYPE;
    v_edited  boolean;
BEGIN
    IF p_decision NOT IN ('approve', 'reject') THEN
        RAISE EXCEPTION 'decision must be approve or reject, got %', p_decision;
    END IF;

    SELECT * INTO v_row FROM followups f
     WHERE f.id = p_id AND f.approval_token::text = p_token AND f.status = 'pending_approval'
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN;   -- unknown link, wrong token, or already decided
    END IF;

    v_edited := p_decision = 'approve'
                AND (coalesce(nullif(p_subject, ''), v_row.subject) IS DISTINCT FROM v_row.subject
                  OR coalesce(nullif(p_body, ''), v_row.body) IS DISTINCT FROM v_row.body);

    UPDATE followups f
       SET status     = CASE WHEN p_decision = 'approve' THEN 'sending' ELSE 'rejected' END,
           subject    = CASE WHEN p_decision = 'approve' THEN coalesce(nullif(p_subject, ''), f.subject) ELSE f.subject END,
           body       = CASE WHEN p_decision = 'approve' THEN coalesce(nullif(p_body, ''), f.body) ELSE f.body END,
           decided_by = p_by, decided_at = now(), updated_at = now()
     WHERE f.id = p_id;

    INSERT INTO activity_log (account_id, followup_id, event, detail)
    VALUES (v_row.account_id, p_id,
            CASE WHEN p_decision = 'approve' THEN 'approved' ELSE 'rejected' END,
            'by ' || coalesce(p_by, 'unknown') || CASE WHEN v_edited THEN ' (edited before approval)' ELSE '' END);

    RETURN QUERY
    SELECT f.id, p_decision, a.id, a.company, a.contact_name, a.email, f.subject, f.body, a.sales_rep
    FROM followups f JOIN accounts a ON a.id = f.account_id
    WHERE f.id = p_id;
END
$fn$;

CREATE OR REPLACE FUNCTION mark_sent(p_id int, p_channel text)
RETURNS TABLE (followup_id int, status text)
LANGUAGE plpgsql AS $fn$
#variable_conflict use_column
BEGIN
    RETURN QUERY
    WITH upd AS (
        UPDATE followups f SET status = 'sent', sent_at = now(), updated_at = now(), error = NULL
         WHERE f.id = p_id AND f.status = 'sending'
        RETURNING f.id, f.account_id, f.status
    ), acc AS (
        UPDATE accounts a SET last_contacted_at = now()
         WHERE a.id IN (SELECT upd.account_id FROM upd)
    ), log AS (
        INSERT INTO activity_log (account_id, followup_id, event, detail)
        SELECT upd.account_id, upd.id, 'sent', 'via ' || p_channel FROM upd
    )
    SELECT upd.id, upd.status FROM upd;
END
$fn$;

CREATE OR REPLACE FUNCTION mark_send_failed(p_id int, p_error text)
RETURNS TABLE (followup_id int, status text)
LANGUAGE plpgsql AS $fn$
#variable_conflict use_column
BEGIN
    RETURN QUERY
    WITH upd AS (
        UPDATE followups f SET status = 'send_failed', error = p_error, updated_at = now()
         WHERE f.id = p_id AND f.status = 'sending'
        RETURNING f.id, f.account_id, f.status
    ), log AS (
        INSERT INTO activity_log (account_id, followup_id, event, detail)
        SELECT upd.account_id, upd.id, 'send_failed', p_error FROM upd
    )
    SELECT upd.id, upd.status FROM upd;
END
$fn$;

-- What the approval page lists.
CREATE VIEW approval_queue AS
SELECT f.id, f.approval_token, f.kind, f.subject, f.body, f.drafted_by, f.created_at,
       a.company, a.contact_name, a.email, a.sales_rep, a.segment, a.city
FROM followups f JOIN accounts a ON a.id = f.account_id
WHERE f.status = 'pending_approval'
ORDER BY f.kind DESC, a.company;
