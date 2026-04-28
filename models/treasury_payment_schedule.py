from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class TreasuryPaymentSchedule(models.Model):
    _name = 'rfh.treasury.payment.schedule'
    _description = 'Treasury Payment Schedule'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'priority_sequence asc, scheduled_date asc, due_date asc, id desc'

    # ------------------------------------------------------------------
    # Identity / sequence
    # ------------------------------------------------------------------

    name = fields.Char(
        string='Reference',
        required=True,
        readonly=True,
        copy=False,
        default=lambda self: _('New'),
        tracking=True,
    )

    # ------------------------------------------------------------------
    # Source document (generic pointer)
    # ------------------------------------------------------------------

    source_model = fields.Char(
        string='Source Model',
        required=True,
        readonly=True,
        tracking=True,
    )
    source_res_id = fields.Integer(
        string='Source Record ID',
        required=True,
        readonly=True,
        tracking=True,
    )
    source_reference = fields.Char(
        string='Source Reference',
        readonly=True,
        tracking=True,
    )
    source_document_url = fields.Char(
        string='Source Document URL',
        compute='_compute_source_document_url',
    )

    # ------------------------------------------------------------------
    # Payee
    # ------------------------------------------------------------------

    partner_id = fields.Many2one(
        'res.partner',
        string='Payee / Vendor',
        required=True,
        tracking=True,
        index=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
        tracking=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        required=True,
        default=lambda self: self.env.company.currency_id,
    )

    # ------------------------------------------------------------------
    # Amounts
    # ------------------------------------------------------------------

    amount = fields.Monetary(
        string='Amount',
        required=True,
        currency_field='currency_id',
        tracking=True,
    )
    amount_paid = fields.Monetary(
        string='Amount Paid',
        currency_field='currency_id',
        tracking=True,
    )
    balance = fields.Monetary(
        string='Balance',
        compute='_compute_balance',
        store=True,
        currency_field='currency_id',
    )

    # ------------------------------------------------------------------
    # Narrative
    # ------------------------------------------------------------------

    description = fields.Text(string='Description')
    notes = fields.Text(string='Treasury Notes')

    # ------------------------------------------------------------------
    # Dates
    # ------------------------------------------------------------------

    due_date = fields.Date(string='Due Date', tracking=True)
    scheduled_date = fields.Date(string='Scheduled Payment Date', tracking=True)
    payment_date = fields.Date(string='Actual Payment Date', tracking=True)

    # ------------------------------------------------------------------
    # Priority
    # ------------------------------------------------------------------

    priority = fields.Selection([
        ('low', 'Low'),
        ('normal', 'Normal'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
        ('critical', 'Critical'),
    ], string='Priority', default='normal', tracking=True, index=True)

    priority_sequence = fields.Integer(
        string='Priority Sequence',
        compute='_compute_priority_sequence',
        store=True,
    )
    priority_color = fields.Integer(
        string='Color',
        compute='_compute_priority_color',
        store=True,
    )

    # ------------------------------------------------------------------
    # State / workflow
    # ------------------------------------------------------------------

    state = fields.Selection([
        ('draft', 'Draft'),
        ('queued', 'Queued'),
        ('scheduled', 'Scheduled'),
        ('batched', 'Batched'),
        ('approved', 'Approved for Payment'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
        ('failed', 'Failed'),
    ], string='Status', default='draft', tracking=True, index=True)

    batch_id = fields.Many2one(
        'rfh.treasury.payment.batch',
        string='Payment Batch',
        readonly=True,
        copy=False,
        tracking=True,
    )

    # ------------------------------------------------------------------
    # Payment details
    # ------------------------------------------------------------------

    journal_id = fields.Many2one(
        'account.journal',
        string='Payment Journal',
        domain="[('type', 'in', ['bank', 'cash'])]",
        tracking=True,
    )
    payment_reference = fields.Char(string='Payment Reference', tracking=True)
    payment_method = fields.Selection([
        ('bank_transfer', 'Bank Transfer'),
        ('cash', 'Cash'),
        ('cheque', 'Cheque'),
        ('online', 'Online Payment'),
        ('flutterwave', 'Flutterwave'),
        ('other', 'Other'),
    ], string='Payment Method', default='bank_transfer')

    account_payment_id = fields.Many2one(
        'account.payment',
        string='Odoo Payment',
        readonly=True,
        copy=False,
    )
    origin_payment_state = fields.Char(
        string='Origin Payment State',
        readonly=True,
    )

    active = fields.Boolean(default=True)

    # ------------------------------------------------------------------
    # Computed fields
    # ------------------------------------------------------------------

    @api.depends('amount', 'amount_paid')
    def _compute_balance(self):
        for rec in self:
            rec.balance = rec.amount - (rec.amount_paid or 0.0)

    @api.depends('priority')
    def _compute_priority_sequence(self):
        seq_map = {'critical': 1, 'urgent': 2, 'high': 3, 'normal': 4, 'low': 5}
        for rec in self:
            rec.priority_sequence = seq_map.get(rec.priority, 4)

    @api.depends('priority')
    def _compute_priority_color(self):
        color_map = {'low': 0, 'normal': 4, 'high': 2, 'urgent': 1, 'critical': 6}
        for rec in self:
            rec.priority_color = color_map.get(rec.priority, 0)

    def _compute_source_document_url(self):
        for rec in self:
            if rec.source_model and rec.source_res_id:
                rec.source_document_url = (
                    f'/web#model={rec.source_model}&id={rec.source_res_id}&view_type=form'
                )
            else:
                rec.source_document_url = False

    # ------------------------------------------------------------------
    # ORM overrides
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'rfh.treasury.payment.schedule'
                ) or _('New')
        return super().create(vals_list)

    def unlink(self):
        if any(rec.state == 'paid' for rec in self):
            raise UserError(_('Paid payment schedules cannot be deleted.'))
        return super().unlink()

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    @api.constrains('amount')
    def _check_amount(self):
        for rec in self:
            if rec.amount <= 0:
                raise ValidationError(_('Amount must be greater than zero.'))

    @api.constrains('amount_paid', 'amount')
    def _check_amount_paid(self):
        for rec in self:
            if rec.amount_paid and rec.amount_paid > rec.amount:
                raise ValidationError(_('Amount paid cannot exceed the scheduled amount.'))

    # ------------------------------------------------------------------
    # Workflow actions
    # ------------------------------------------------------------------

    def action_queue(self):
        for rec in self:
            if rec.state not in ('draft', 'failed'):
                raise UserError(_('Only draft or failed schedules can be queued.'))
            rec.state = 'queued'
            rec.message_post(body=_('Payment queued for Treasury processing.'))

    def action_schedule(self):
        for rec in self:
            if rec.state != 'queued':
                raise UserError(_('Only queued schedules can be scheduled.'))
            rec.state = 'scheduled'
            rec.message_post(
                body=_('Payment scheduled for %s.', rec.scheduled_date or _('(no date set)'))
            )

    def action_approve(self):
        for rec in self:
            if rec.state not in ('scheduled', 'batched'):
                raise UserError(_('Only scheduled or batched payments can be approved.'))
            rec.state = 'approved'
            rec.message_post(body=_('Payment approved for disbursement.'))

    def action_mark_paid(self):
        for rec in self:
            if rec.state == 'cancelled':
                raise UserError(_('Cancelled payments cannot be marked as paid.'))
            if not rec.payment_date:
                raise UserError(_('Please set the Actual Payment Date before marking as paid.'))
            if not rec.journal_id:
                raise UserError(_('Please select a Payment Journal before marking as paid.'))

            if not rec.amount_paid:
                rec.amount_paid = rec.amount

            rec._create_accounting_payment()
            rec.state = 'paid'
            rec.message_post(
                body=_(
                    'Payment marked as paid. Amount: %s %s. Reference: %s.',
                    rec.amount_paid,
                    rec.currency_id.name,
                    rec.payment_reference or _('N/A'),
                )
            )
            rec._trigger_origin_paid_hook()

    def action_cancel(self):
        for rec in self:
            if rec.state == 'paid':
                raise UserError(_('Paid schedules cannot be cancelled.'))
            rec.state = 'cancelled'
            if rec.batch_id and rec.batch_id.state not in ('paid', 'cancelled'):
                rec.batch_id = False
            rec.message_post(body=_('Payment schedule cancelled.'))

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state not in ('cancelled',):
                raise UserError(_('Only cancelled schedules can be reset to draft.'))
            rec.state = 'draft'
            rec.message_post(body=_('Payment schedule reset to draft.'))

    # ------------------------------------------------------------------
    # Accounting payment creation (used for vendor bills)
    # ------------------------------------------------------------------

    def _create_accounting_payment(self):
        """Create an account.payment when source is account.move (vendor bill)."""
        self.ensure_one()
        if self.source_model != 'account.move' or not self.source_res_id:
            return

        move = self.env['account.move'].browse(self.source_res_id).exists()
        if not move or move.move_type not in ('in_invoice', 'in_refund'):
            return

        if self.account_payment_id:
            return

        payment = self.env['account.payment'].create({
            'payment_type': 'outbound',
            'partner_type': 'supplier',
            'partner_id': self.partner_id.id,
            'amount': self.amount_paid or self.amount,
            'currency_id': self.currency_id.id,
            'date': self.payment_date,
            'journal_id': self.journal_id.id,
            'ref': self.payment_reference or self.name,
        })
        payment.action_post()
        self.account_payment_id = payment.id

        # Attempt reconciliation
        try:
            payable_lines = payment.line_ids.filtered(
                lambda l: l.account_id.account_type in (
                    'liability_payable',
                )
            )
            move_lines = move.line_ids.filtered(
                lambda l: l.account_id.account_type in (
                    'liability_payable',
                ) and not l.reconciled
            )
            if payable_lines and move_lines:
                (payable_lines + move_lines).reconcile()
                self.message_post(body=_('Vendor bill reconciled with Odoo payment %s.', payment.name))
        except Exception as e:
            self.message_post(body=_('Reconciliation attempt failed: %s', str(e)))

    # ------------------------------------------------------------------
    # Generic origin hook trigger
    # ------------------------------------------------------------------

    def _trigger_origin_paid_hook(self):
        for rec in self:
            if not rec.source_model or not rec.source_res_id:
                continue
            if rec.source_model == 'manual':
                continue
            try:
                origin = self.env[rec.source_model].browse(rec.source_res_id).exists()
                if not origin:
                    rec.message_post(body=_('Origin document no longer exists.'))
                    continue
                if hasattr(origin, 'action_treasury_payment_completed'):
                    origin.action_treasury_payment_completed(rec)
                    rec.message_post(
                        body=_('Origin document payment hook executed successfully.')
                    )
                else:
                    if hasattr(origin, 'message_post'):
                        origin.message_post(
                            body=_('Treasury payment %s has been marked as paid.', rec.name)
                        )
                    rec.message_post(
                        body=_(
                            'Origin model has no action_treasury_payment_completed hook. '
                            'Chatter notification posted only.'
                        )
                    )
            except Exception as e:
                rec.write({'state': 'failed'})
                rec.message_post(body=_('Origin payment hook failed: %s', str(e)))

    # ------------------------------------------------------------------
    # Smart-button helper (used from PO / move views)
    # ------------------------------------------------------------------

    def action_view_treasury_payment_schedules(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Treasury Payments'),
            'res_model': 'rfh.treasury.payment.schedule',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.ids)],
            'context': {},
        }
