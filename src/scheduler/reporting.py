# src/scheduler/reporting.py
from collections import defaultdict

def print_delivery_analysis(scheduler, scenario_name=""):
    """Print detailed delivery analysis for all products"""
    metrics = scheduler.calculate_lateness_metrics()

    print(f"\n{'=' * 80}")
    print(f"DELIVERY ANALYSIS{f' - {scenario_name}' if scenario_name else ''}")
    print(f"{'=' * 80}")
    print(f"{'Product':<12} {'Due Date':<12} {'Completion':<12} {'Delta':<8} {'Status':<15}")
    print("-" * 80)

    max_lateness = -float('inf')
    min_lateness = float('inf')

    for product in sorted(metrics.keys()):
        data = metrics[product]

        if data['projected_completion']:
            due_date = data['delivery_date'].strftime('%Y-%m-%d')
            completion_date = data['projected_completion'].strftime('%Y-%m-%d')
            lateness = data['lateness_days']

            # Track max and min
            if lateness < 999999:
                max_lateness = max(max_lateness, lateness)
                min_lateness = min(min_lateness, lateness)

            # Format delta with sign
            if lateness > 0:
                delta_str = f"+{lateness}d"
                status = f"LATE"
                status_color = "❌"
            elif lateness < 0:
                delta_str = f"{lateness}d"
                status = f"EARLY"
                status_color = "✅"
            else:
                delta_str = "0d"
                status = f"ON TIME"
                status_color = "✓"

            print(f"{product:<12} {due_date:<12} {completion_date:<12} {delta_str:<8} {status_color} {status:<12}")
        else:
            print(
                f"{product:<12} {data['delivery_date'].strftime('%Y-%m-%d'):<12} {'UNSCHEDULED':<12} {'N/A':<8} ❓ UNSCHEDULED")

    print("-" * 80)
    print(f"Maximum Lateness (worst product): {max_lateness:+.0f} days")
    print(f"Minimum Lateness (best product): {min_lateness:+.0f} days")
    if hasattr(scheduler, 'scenario_3_target'):
        print(f"Target in Scenario 3: {scheduler.scenario_3_target}")
    else:
        print("Target in Scenario 3: N/A")
    print("=" * 80)

    return max_lateness

def identify_product_bottlenecks(scheduler, product):
    """Identify which teams are bottlenecks for a specific product"""
    bottleneck_teams = defaultdict(int)

    # Find all tasks for this product
    product_tasks = [(tid, info) for tid, info in scheduler.task_schedule.items()
                     if info.get('product') == product]

    if not product_tasks:
        return []

    # Count task minutes per team
    for task_id, schedule in product_tasks:
        team = schedule.get('team_skill', schedule.get('team'))
        if team:
            # Weight by duration and mechanics required
            workload = schedule['duration'] * schedule.get('mechanics_required', 1)
            bottleneck_teams[team] += workload

    # Sort by workload to find bottlenecks
    sorted_teams = sorted(bottleneck_teams.items(), key=lambda x: x[1], reverse=True)

    return sorted_teams[:5]  # Return top 5 bottleneck teams

def identify_task_relationships(scheduler):
    """Identify tasks with no predecessors/successors"""
    all_tasks = set(range(1, 101))  # Assuming tasks 1-100

    first_tasks = set()
    second_tasks = set()

    for constraint in scheduler.precedence_constraints:
        first_tasks.add(constraint['First'])
        second_tasks.add(constraint['Second'])

    no_predecessors = all_tasks - second_tasks
    no_successors = all_tasks - first_tasks
    orphaned = all_tasks - first_tasks - second_tasks

    print(f"Tasks with no predecessors: {sorted(no_predecessors)}")
    print(f"Tasks with no successors: {sorted(no_successors)}")
    print(f"Orphaned tasks (no relationships): {sorted(orphaned)}")
