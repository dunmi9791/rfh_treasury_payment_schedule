from odoo import api, fields, models, _
from odoo.exceptions import UserError

# hr_expense is an optional dependency. If the module is not installed,
# this file still imports cleanly because the _inherit will silently
# do nothing when 'hr.expense.sheet' does not exist in the registry.
# The manifest lists hr_expense as a hard dependency; remove it from
# 'depends' and comment out this file's _inherit if you need hr_expense
# to be truly optional.


class HrExpenseSheet(models.Model):
    _inherit = ['hr.expense.sheet', 'rfh.treasury.payment.origin.mixin']
    _name = 'hr.expense.sheet'

    treasury_payment_count = fields.Integer(
        string='Treasury Payments',
        compute='_compute_treasury_payment_count',
    )

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------

    def _compute_treasury_payment_count(self):
        Schedule = self.env['rfh.treasury.payment.schedule']
        for sheet in self:
            sheet.treasury_payment_count = Schedule.search_count([
                ('source_model', '=', 'hr.expense.sheet'),
                ('source_res_id', '=', sheet.id),
            ])

    # ------------------------------------------------------------------
    # Mixin implementation
    # ------------------------------------------------------------------

    def _prepare_treasury_payment_schedule_vals(self):
        self.ensure_one()
        if self.state not in ('approve', 'post', 'done'):
            raise UserError(
                _('Only approved expense sheets can be queued for Treasury payment.')
            )
        payment_state = getattr(self, 'payment_state', None)
        if payment_state == 'paid':
            raise UserError(_('This expense sheet has already been paid.'))

        employee = self.employee_id
        partner = (
            employee.user_id.partner_id
            if employee.user_id
            else employee.address_home_id
        )
        if not partner:
            raise UserError(
                _('Employee %s has no linked partner. Please set a home address.', employee.name)
            )

        return {
            'source_model': 'hr.expense.sheet',
            'source_res_id': self.id,
            'source_reference': self.name,
            'partner_id': partner.id,
            'amount': self.total_amount,
            'currency_id': self.currency_id.id,
            'company_id': self.company_id.id,
            'due_date': fields.Date.today(),
            'description': _('Expense reimbursement: %s', self.name),
            'state': 'queued',
        }

    def action_create_treasury_payment_schedule(self):
        self.ensure_one()
        return super().action_create_treasury_payment_schedule()

    def action_treasury_payment_completed(self, payment_schedule):
        self.ensure_one()
        self.message_post(
            body=_('Treasury payment completed: %s', payment_schedule.name)
        )
        # If Odoo has registered the expense as paid via account flow, do not
        # double-register. Otherwise post a message.
        if hasattr(self, 'set_to_paid') and self.state not in ('done',):
            try:
                self.set_to_paid()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Smart button
    # ------------------------------------------------------------------

    def action_view_treasury_payments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Treasury Payments'),
            'res_model': 'rfh.treasury.payment.schedule',
            'view_mode': 'list,form',
            'domain': [
                ('source_model', '=', 'hr.expense.sheet'),
                ('source_res_id', '=', self.id),
            ],
        }
