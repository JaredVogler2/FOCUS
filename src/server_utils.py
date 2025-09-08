# src/server_utils.py

import platform
import subprocess
import socket
import time
import sys

def kill_port(port=5000):
    system = platform.system()
    try:
        if system == 'Windows':
            command = f'netstat -ano | findstr :{port}'
            result = subprocess.run(command, shell=True, capture_output=True, text=True)
            if result.stdout:
                lines = result.stdout.strip().split('\n')
                for line in lines:
                    if f':{port}' in line and 'LISTENING' in line:
                        parts = line.split()
                        pid = parts[-1]
                        kill_command = f'taskkill /F /PID {pid}'
                        subprocess.run(kill_command, shell=True, capture_output=True)
                        print(f"Killed process {pid} using port {port}")
                        time.sleep(1)
        else:
            command = f'lsof -ti:{port}'
            result = subprocess.run(command, shell=True, capture_output=True, text=True)
            if result.stdout:
                pid = result.stdout.strip()
                kill_command = f'kill -9 {pid}'
                subprocess.run(kill_command, shell=True)
                print(f"Killed process {pid} using port {port}")
                time.sleep(1)
    except Exception as e:
        print(f"Warning: Could not auto-kill port {port}: {e}")

def check_and_kill_port(port=5000):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('127.0.0.1', port))
    sock.close()
    if result == 0:
        print(f"Port {port} is in use. Attempting to free it...")
        kill_port(port)
        time.sleep(1)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        if result == 0:
            print(f"Failed to free port {port}. Please manually kill the process.")
            sys.exit(1)
        else:
            print(f"Port {port} successfully freed!")


from collections import defaultdict
import re
from datetime import datetime


def export_scenario_with_capacities(scheduler, scenario_name):
    """Export scenario results including current team capacities and shift information"""
    team_capacities = {**scheduler.team_capacity, **scheduler.quality_team_capacity, **scheduler.customer_team_capacity}
    team_shifts = {**scheduler.team_shifts, **scheduler.quality_team_shifts, **scheduler.customer_team_shifts}

    tasks = []
    MAX_TASKS_FOR_DASHBOARD = 1000
    total_tasks_available = len(scheduler.global_priority_list) if hasattr(scheduler, 'global_priority_list') else len(scheduler.task_schedule)

    if hasattr(scheduler, 'global_priority_list') and scheduler.global_priority_list:
        sorted_priority_items = sorted(scheduler.global_priority_list, key=lambda x: x.get('global_priority', 999))[:MAX_TASKS_FOR_DASHBOARD]
        for item in sorted_priority_items:
            task_id = item.get('task_instance_id')
            if task_id in scheduler.task_schedule:
                schedule = scheduler.task_schedule[task_id]
                task_info = scheduler.tasks.get(task_id, {})
                tasks.append({
                    'taskId': task_id,
                    'type': item.get('task_type', 'Production'),
                    'product': item.get('product_line', 'Unknown'),
                    'team': schedule.get('team', ''),
                    'teamSkill': schedule.get('team_skill', ''),
                    'skill': schedule.get('skill', ''),
                    'startTime': schedule['start_time'].isoformat(),
                    'endTime': schedule['end_time'].isoformat(),
                    'duration': schedule.get('duration', 60),
                    'mechanics': schedule.get('mechanics_required', 1),
                    'personnel_required': schedule.get('personnel_required'),
                    'shift': schedule.get('shift', '1st'),
                    'priority': item.get('global_priority', 999),
                    'isLatePartTask': task_id in scheduler.late_part_tasks,
                    'isReworkTask': task_id in scheduler.rework_tasks,
                    'isQualityTask': schedule.get('is_quality', False),
                    'isCustomerTask': schedule.get('is_customer', False),
                    'isCritical': item.get('slack_hours', 999) < 24,
                    'slackHours': item.get('slack_hours', 999)
                })

    makespan = scheduler.calculate_makespan()
    lateness_metrics = scheduler.calculate_lateness_metrics()

    team_task_minutes = defaultdict(int)
    for task_id, schedule in scheduler.task_schedule.items():
        team_for_util = schedule.get('team_skill', schedule.get('team'))
        if team_for_util:
            required = schedule.get('mechanics_required') or schedule.get('personnel_required') or 1
            team_task_minutes[team_for_util] += schedule.get('duration', 0) * required

    utilization = {team: min(100, round((team_task_minutes.get(team, 0) / (8 * 60 * makespan * capacity) * 100), 1)) if capacity > 0 and makespan > 0 else 0 for team, capacity in team_capacities.items()}

    products = []
    today = datetime.now()
    for product, metrics in lateness_metrics.items():
        # Calculate days remaining
        days_remaining = 0
        if metrics['projected_completion']:
            days_remaining = (metrics['projected_completion'] - today).days
            if days_remaining < 0:
                days_remaining = 0

        # Calculate critical path length for the product
        critical_path_count = 0
        if hasattr(scheduler, 'global_priority_list') and scheduler.global_priority_list:
            for task in scheduler.global_priority_list:
                if task.get('product_line') == product and task.get('criticality') == 'CRITICAL':
                    critical_path_count += 1

        # Calculate progress
        tasks_for_product = [t for t in scheduler.task_schedule.values() if t.get('product') == product]
        completed_tasks = [t for t in tasks_for_product if t['end_time'] < today]
        progress = round(len(completed_tasks) / len(tasks_for_product) * 100) if tasks_for_product else 0


        products.append({
            'name': product,
            'totalTasks': metrics['total_tasks'],
            'deliveryDate': metrics['delivery_date'].isoformat(),
            'projectedCompletion': metrics['projected_completion'].isoformat() if metrics['projected_completion'] else '',
            'onTime': metrics['on_time'],
            'latenessDays': metrics['lateness_days'] if metrics['lateness_days'] < 999999 else 0,
            'daysRemaining': days_remaining,
            'criticalPath': critical_path_count,
            'progress': progress,
            'latePartsCount': metrics.get('task_breakdown', {}).get('Late Part', 0),
            'reworkCount': metrics.get('task_breakdown', {}).get('Rework', 0),
            'customerCount': metrics.get('task_breakdown', {}).get('Customer', 0)
        })

    on_time_rate = round(sum(1 for p in products if p['onTime']) / len(products) * 100 if products else 0, 1)
    max_lateness = max((p['latenessDays'] for p in products if p['latenessDays'] < 999999), default=0)
    total_workforce = sum(team_capacities.values())

    # --- Aggregated Stats for Dashboard Labels ---
    agg_stats = {
        'by_role': defaultdict(lambda: {'teams': 0, 'workers': 0}),
        'by_skill': defaultdict(lambda: {'teams': 0, 'workers': 0}),
    }

    # Regex to extract skill from team names like "Mechanic Team 1 (Skill 1)"
    skill_pattern = re.compile(r'.*\(Skill (\d+)\)')

    for team, capacity in team_capacities.items():
        # By Role
        role = "Mechanic"
        if "Quality" in team:
            role = "Quality"
        elif "Customer" in team:
            role = "Customer"
        agg_stats['by_role'][role]['teams'] += 1
        agg_stats['by_role'][role]['workers'] += capacity

        # By Skill
        match = skill_pattern.match(team)
        if match:
            skill_num = match.group(1)
            skill_name = f"Skill {skill_num}"
            agg_stats['by_skill'][skill_name]['teams'] += 1
            agg_stats['by_skill'][skill_name]['workers'] += capacity

    return {
        'scenarioId': scenario_name,
        'tasks': tasks,
        'teamCapacities': team_capacities,
        'teamShifts': team_shifts,
        'products': products,
        'utilization': utilization,
        'totalWorkforce': total_workforce,
        'avgUtilization': round(sum(utilization.values()) / len(utilization) if utilization else 0, 1),
        'makespan': makespan,
        'onTimeRate': on_time_rate,
        'maxLateness': max_lateness,
        'totalTasks': total_tasks_available,
        'displayedTasks': len(tasks),
        'truncated': total_tasks_available > MAX_TASKS_FOR_DASHBOARD,
        'aggStats': agg_stats
    }
