from odoo import api, fields, models, _
from odoo.exceptions import UserError


class TreasuryPaymentBatch(models.Model):
    _name = 'rfh.treasury.payment.batch'
    _description = 'Treasury Payment Batch'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(
        string='Batch Reference',
        required=True,
        readonly=True,
        copy=False,
        default=lambda self: _('New'),
        tracking=True,
    )
    date = fields.Date(
        string='Batch Date',
        default=fields.Date.context_today,
        required=True,
    )
    scheduled_date = fields.Date(string='Scheduled Payment Date')
    payment_date = fields.Date(string='Payment Date', tracking=True)

    line_ids = fields.One2many(
        'rfh.treasury.payment.schedule',
        'batch_id',
        string='Scheduled Payments',
    )
    line_count = fields.Integer(
        string='Lines',
        compute='_compute_line_count',
    )

    company_id = fields.Many2one(
        'res.company',
        default=lambda self: self.env.company,
        required=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    total_amount = fields.Monetary(
        string='Total Amount',
        compute='_compute_total_amount',
        store=True,
        currency_field='currency_id',
    )

    journal_id = fields.Many2one(
        'account.journal',
        string='Payment Journal',
        domain="[('type', 'in', ['bank', 'cash'])]",
        tracking=True,
    )
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    ], default='draft', tracking=True)

    payment_reference = fields.Char(string='Batch Payment Reference', tracking=True)
    notes = fields.Text()

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------

    @api.depends('line_ids.amount')
    def _compute_total_amount(self):
        for batch in self:
            batch.total_amount = sum(batch.line_ids.mapped('amount'))

    @api.depends('line_ids')
    def _compute_line_count(self):
        for batch in self:
            batch.line_count = len(batch.line_ids)

    # ------------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'rfh.treasury.payment.batch'
                ) or _('New')
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------

    def action_submit(self):
        for batch in self:
            if batch.state != 'draft':
                raise UserError(_('Only draft batches can be submitted.'))
            if not batch.line_ids:
                raise UserError(_('Cannot submit an empty batch.'))
            batch.state = 'submitted'
            batch.message_post(body=_('Batch submitted for approval.'))

    def action_approve(self):
        for batch in self:
            if batch.state != 'submitted':
                raise UserError(_('Only submitted batches can be approved.'))
            batch.state = 'approved'
            batch.message_post(body=_('Batch approved for payment.'))

    def action_mark_paid(self):
        for batch in self:
            if batch.state != 'approved':
                raise UserError(_('Only approved batches can be marked as paid.'))
            if not batch.payment_date:
                raise UserError(_('Please set the Payment Date before marking the batch as paid.'))
            if not batch.journal_id:
                raise UserError(_('Please select a Payment Journal before marking the batch as paid.'))

            for line in batch.line_ids:
                if line.state in ('paid', 'cancelled'):
                    continue
                if not line.amount_paid:
                    line.amount_paid = line.amount
                line.payment_date = batch.payment_date
                line.journal_id = batch.journal_id.id
                if not line.payment_reference:
                    line.payment_reference = batch.payment_reference or batch.name
                line._create_accounting_payment()
                line.state = 'paid'
                line.message_post(
                    body=_('Paid via batch %s.', batch.name)
                )
                line._trigger_origin_paid_hook()

            batch.state = 'paid'
            batch.message_post(
                body=_('Batch marked as paid. %d lines processed.', len(batch.line_ids))
            )

    def action_cancel(self):
        for batch in self:
            if batch.state == 'paid':
                raise UserError(_('Paid batches cannot be cancelled.'))
            for line in batch.line_ids:
                if line.state not in ('paid', 'cancelled'):
                    line.batch_id = False
                    line.state = 'queued'
            batch.state = 'cancelled'
            batch.message_post(body=_('Batch cancelled. Lines returned to queued.'))

    # ------------------------------------------------------------------
    # Smart button
    # ------------------------------------------------------------------

    def action_view_lines(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Payment Lines'),
            'res_model': 'rfh.treasury.payment.schedule',
            'view_mode': 'list,form',
            'domain': [('batch_id', '=', self.id)],
        }
