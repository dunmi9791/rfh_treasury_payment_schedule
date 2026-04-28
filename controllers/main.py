import json
from datetime import datetime

from odoo import http, _
from odoo.http import request
from odoo.exceptions import AccessError, UserError, ValidationError


def _json_response(data, status=200):
    return request.make_response(
        json.dumps(data),
        headers=[('Content-Type', 'application/json')],
        status=status,
    )


def _error(message, status=400):
    return _json_response({'success': False, 'error': message}, status=status)


def _parse_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return None


class TreasuryPaymentController(http.Controller):

    # ------------------------------------------------------------------
    # GET / LIST schedules
    # ------------------------------------------------------------------

    @http.route('/api/treasury/payment_schedules', type='json', auth='user', methods=['POST'], csrf=False)
    def get_payment_schedules(self, **kwargs):
        try:
            params = request.get_json_data() or {}
            domain = [('company_id', '=', request.env.company.id)]

            if params.get('state'):
                domain.append(('state', '=', params['state']))
            if params.get('priority'):
                domain.append(('priority', '=', params['priority']))
            if params.get('partner_id'):
                domain.append(('partner_id', '=', params['partner_id']))
            if params.get('date_from'):
                d = _parse_date(params['date_from'])
                if d:
                    domain.append(('due_date', '>=', str(d)))
            if params.get('date_to'):
                d = _parse_date(params['date_to'])
                if d:
                    domain.append(('due_date', '<=', str(d)))

            limit = min(int(params.get('limit', 50)), 500)
            offset = int(params.get('offset', 0))

            schedules = request.env['rfh.treasury.payment.schedule'].search(
                domain, limit=limit, offset=offset,
                order='priority_sequence asc, scheduled_date asc'
            )

            data = []
            for s in schedules:
                data.append({
                    'id': s.id,
                    'name': s.name,
                    'source_model': s.source_model,
                    'source_reference': s.source_reference or '',
                    'partner': s.partner_id.name,
                    'amount': s.amount,
                    'balance': s.balance,
                    'currency': s.currency_id.name,
                    'priority': s.priority,
                    'state': s.state,
                    'due_date': str(s.due_date) if s.due_date else None,
                    'scheduled_date': str(s.scheduled_date) if s.scheduled_date else None,
                    'payment_date': str(s.payment_date) if s.payment_date else None,
                    'batch_id': s.batch_id.id if s.batch_id else None,
                    'batch_name': s.batch_id.name if s.batch_id else None,
                })
            return {'success': True, 'data': data, 'total': len(data)}

        except AccessError:
            return {'success': False, 'error': 'Access denied'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # ------------------------------------------------------------------
    # Schedule a payment (set date / priority)
    # ------------------------------------------------------------------

    @http.route('/api/treasury/payment_schedule/schedule', type='json', auth='user', methods=['POST'], csrf=False)
    def schedule_payment(self, **kwargs):
        try:
            params = request.get_json_data() or {}
            schedule_id = params.get('schedule_id')
            if not schedule_id:
                return {'success': False, 'error': 'schedule_id is required'}

            schedule = request.env['rfh.treasury.payment.schedule'].browse(int(schedule_id))
            if not schedule.exists():
                return {'success': False, 'error': 'Schedule not found'}

            write_vals = {}
            if params.get('scheduled_date'):
                d = _parse_date(params['scheduled_date'])
                if d:
                    write_vals['scheduled_date'] = str(d)
            if params.get('priority'):
                write_vals['priority'] = params['priority']

            if write_vals:
                schedule.write(write_vals)

            if schedule.state == 'queued':
                schedule.action_schedule()
            elif schedule.state == 'draft':
                schedule.action_queue()
                if write_vals.get('scheduled_date'):
                    schedule.action_schedule()

            return {'success': True, 'name': schedule.name, 'state': schedule.state}

        except (AccessError, UserError, ValidationError) as e:
            return {'success': False, 'error': str(e)}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # ------------------------------------------------------------------
    # Mark individual schedule as paid
    # ------------------------------------------------------------------

    @http.route('/api/treasury/payment_schedule/mark_paid', type='json', auth='user', methods=['POST'], csrf=False)
    def mark_paid(self, **kwargs):
        try:
            params = request.get_json_data() or {}
            schedule_id = params.get('schedule_id')
            if not schedule_id:
                return {'success': False, 'error': 'schedule_id is required'}

            schedule = request.env['rfh.treasury.payment.schedule'].browse(int(schedule_id))
            if not schedule.exists():
                return {'success': False, 'error': 'Schedule not found'}

            if params.get('payment_date'):
                schedule.payment_date = _parse_date(params['payment_date'])
            if params.get('journal_id'):
                schedule.journal_id = int(params['journal_id'])
            if params.get('payment_reference'):
                schedule.payment_reference = params['payment_reference']
            if params.get('amount_paid'):
                schedule.amount_paid = float(params['amount_paid'])

            schedule.action_mark_paid()
            return {'success': True, 'name': schedule.name, 'state': schedule.state}

        except (AccessError, UserError, ValidationError) as e:
            return {'success': False, 'error': str(e)}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # ------------------------------------------------------------------
    # Create a batch from a list of schedule IDs
    # ------------------------------------------------------------------

    @http.route('/api/treasury/payment_batch/create', type='json', auth='user', methods=['POST'], csrf=False)
    def create_batch(self, **kwargs):
        try:
            params = request.get_json_data() or {}
            schedule_ids = params.get('schedule_ids', [])
            if not schedule_ids:
                return {'success': False, 'error': 'schedule_ids is required'}

            schedules = request.env['rfh.treasury.payment.schedule'].browse(
                [int(i) for i in schedule_ids]
            ).exists()

            invalid = schedules.filtered(lambda s: s.state in ('paid', 'cancelled'))
            if invalid:
                return {
                    'success': False,
                    'error': f'Cannot batch paid/cancelled schedules: {", ".join(invalid.mapped("name"))}'
                }

            batch_vals = {}
            if params.get('scheduled_date'):
                batch_vals['scheduled_date'] = _parse_date(params['scheduled_date'])
            if params.get('journal_id'):
                batch_vals['journal_id'] = int(params['journal_id'])
            if params.get('payment_reference'):
                batch_vals['payment_reference'] = params['payment_reference']

            batch = request.env['rfh.treasury.payment.batch'].create(batch_vals)
            schedules.write({'batch_id': batch.id, 'state': 'batched'})

            return {
                'success': True,
                'batch_id': batch.id,
                'batch_name': batch.name,
                'line_count': len(schedules),
            }

        except (AccessError, UserError, ValidationError) as e:
            return {'success': False, 'error': str(e)}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # ------------------------------------------------------------------
    # Mark a batch as paid
    # ------------------------------------------------------------------

    @http.route('/api/treasury/payment_batch/mark_paid', type='json', auth='user', methods=['POST'], csrf=False)
    def mark_batch_paid(self, **kwargs):
        try:
            params = request.get_json_data() or {}
            batch_id = params.get('batch_id')
            if not batch_id:
                return {'success': False, 'error': 'batch_id is required'}

            batch = request.env['rfh.treasury.payment.batch'].browse(int(batch_id))
            if not batch.exists():
                return {'success': False, 'error': 'Batch not found'}

            if params.get('payment_date'):
                batch.payment_date = _parse_date(params['payment_date'])
            if params.get('payment_reference'):
                batch.payment_reference = params['payment_reference']
            if params.get('journal_id'):
                batch.journal_id = int(params['journal_id'])

            # Ensure approved state
            if batch.state == 'draft':
                batch.action_submit()
            if batch.state == 'submitted':
                batch.action_approve()
            batch.action_mark_paid()

            return {'success': True, 'batch_name': batch.name, 'state': batch.state}

        except (AccessError, UserError, ValidationError) as e:
            return {'success': False, 'error': str(e)}
        except Exception as e:
            return {'success': False, 'error': str(e)}
