# src/scheduler/utils.py

import os
import sys
from datetime import datetime, timedelta

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def debug_print(scheduler, message, force=False):
    """Print debug message if debug mode is enabled or forced"""
    if scheduler.debug or force:
        print(message)

def normalize_relationship_type(relationship):
    """Normalize relationship type strings to standard format"""
    if not relationship:
        return 'Finish <= Start'

    relationship = relationship.strip()

    mappings = {
        'FS': 'Finish <= Start',
        'Finish-Start': 'Finish <= Start',
        'F-S': 'Finish <= Start',
        'F=S': 'Finish = Start',
        'Finish=Start': 'Finish = Start',
        'FF': 'Finish <= Finish',
        'Finish-Finish': 'Finish <= Finish',
        'F-F': 'Finish <= Finish',
        'SS': 'Start <= Start',
        'Start-Start': 'Start <= Start',
        'S-S': 'Start <= Start',
        'S=S': 'Start = Start',
        'Start=Start': 'Start = Start',
        'SF': 'Start <= Finish',
        'Start-Finish': 'Start <= Finish',
        'S-F': 'Start <= Finish'
    }

    return mappings.get(relationship, relationship)

def parse_shift_time(time_str):
    """Helper to parse shift time string into hour and minute"""
    # Remove any whitespace
    time_str = time_str.strip()

    # Handle AM/PM format if present
    if 'AM' in time_str or 'PM' in time_str:
        time_str_clean = time_str.replace(' AM', '').replace(' PM', '').replace('AM', '').replace('PM', '')
        time_parts = time_str_clean.split(':')
        hour = int(time_parts[0])
        minute = int(time_parts[1]) if len(time_parts) > 1 else 0

        # Adjust for PM
        if 'PM' in time_str and hour != 12:
            hour += 12
        elif 'AM' in time_str and hour == 12:
            hour = 0
    else:
        # 24-hour format
        time_parts = time_str.split(':')
        hour = int(time_parts[0])
        minute = int(time_parts[1]) if len(time_parts) > 1 else 0

    return hour, minute

def copy_configuration(config):
    """Create a deep copy of a configuration"""
    return {
        'mechanic': config['mechanic'].copy(),
        'quality': config['quality'].copy()
    }

def is_working_day(scheduler, date, product_line):
    """Check if a date is a working day for a specific product line"""
    if date.weekday() >= 5:  # Weekend
        return False

    # FIX: Handle None or missing product_line
    if not product_line:
        return True  # If no product specified, assume working day

    if product_line not in scheduler.holidays:
        return True  # If product not in holidays dict, assume working day

    # Now safe to check holidays
    if date.date() in [h.date() for h in scheduler.holidays[product_line]]:
        return False

    return True

def check_constraint_satisfied(scheduler, first_schedule, second_schedule, relationship):
    """Check if a scheduling constraint is satisfied between two tasks"""
    if not first_schedule or not second_schedule:
        return True, None, None

    first_start = first_schedule['start_time']
    first_end = first_schedule['end_time']
    second_start = second_schedule['start_time']
    second_end = second_schedule['end_time']
    second_duration = second_schedule['duration']

    relationship = normalize_relationship_type(relationship)

    if relationship == 'Finish <= Start':
        is_satisfied = first_end <= second_start
        earliest_start = first_end
        earliest_end = earliest_start + timedelta(minutes=second_duration)

    elif relationship == 'Finish = Start':
        is_satisfied = abs((first_end - second_start).total_seconds()) < 60
        earliest_start = first_end
        earliest_end = earliest_start + timedelta(minutes=second_duration)

    elif relationship == 'Finish <= Finish':
        is_satisfied = first_end <= second_end
        earliest_end = max(first_end, second_start + timedelta(minutes=second_duration))
        earliest_start = earliest_end - timedelta(minutes=second_duration)

    elif relationship == 'Start <= Start':
        is_satisfied = first_start <= second_start
        earliest_start = first_start
        earliest_end = earliest_start + timedelta(minutes=second_duration)

    elif relationship == 'Start = Start':
        is_satisfied = abs((first_start - second_start).total_seconds()) < 60
        earliest_start = first_start
        earliest_end = earliest_start + timedelta(minutes=second_duration)

    elif relationship == 'Start <= Finish':
        is_satisfied = first_start <= second_end
        earliest_end = max(first_start, second_start + timedelta(minutes=second_duration))
        earliest_start = earliest_end - timedelta(minutes=second_duration)

    else:
        is_satisfied = first_end <= second_start
        earliest_start = first_end
        earliest_end = earliest_start + timedelta(minutes=second_duration)

    return is_satisfied, earliest_start, earliest_end
