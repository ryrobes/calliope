"""Finite field rules shared by business definitions and authoritative commands."""
import datetime as dt
import re
from decimal import Decimal

class FieldErrors(ValueError):
    def __init__(self, fields):
        self.fields=fields
        super().__init__('Check the highlighted fields before continuing.')

def validate(value, *, kind='text', required=False, minimum=None, maximum=None, precision=2, max_length=4000):
    if not isinstance(value,str): return 'Enter a value.'
    v=value.strip()
    if not v: return 'This field is required.' if required else ''
    if len(value)>max_length: return f'Use at most {max_length} characters.'
    if kind in ('money','quantity'):
        if not re.fullmatch(r'-?\d+(?:\.\d+)?',v): return 'Enter a number without commas or symbols.'
        if '.' in v and len(v.split('.')[1])>precision: return f'Use at most {precision} decimal places.'
        number=Decimal(v)
        if minimum is not None and number<Decimal(str(minimum)): return f'Must be at least {minimum}.'
        if maximum is not None and number>Decimal(str(maximum)): return f'Must be no more than {maximum}.'
    elif kind=='date':
        try:
            if dt.date.fromisoformat(v).isoformat()!=v: raise ValueError()
        except ValueError:return 'Use a real date in YYYY-MM-DD format.'
        if minimum and v<minimum:return f'Must be on or after {minimum}.'
        if maximum and v>maximum:return f'Must be on or before {maximum}.'
    elif kind=='time':
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',v):return 'Use a time in HH:MM format.'
    return ''

def checked(fields):
    errors={id:message for id,(value,rules) in fields.items() if (message:=validate(value,**rules))}
    if errors:raise FieldErrors(errors)

def cents(value):return int(Decimal(value.strip())*100)
