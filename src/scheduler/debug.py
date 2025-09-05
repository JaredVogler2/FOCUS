# src/scheduler/debug.py
from collections import defaultdict

def debug_scheduling_blockage(scheduler):
    """Find why scheduling stops at task 140"""

    # Get the dynamic constraints
    constraints = scheduler.build_dynamic_dependencies()

    # Find unscheduled tasks
    unscheduled = [t for t in scheduler.tasks if t not in scheduler.task_schedule]

    print(f"\n[BLOCKAGE ANALYSIS]")
    print(f"Scheduled: {len(scheduler.task_schedule)} tasks")
    print(f"Unscheduled: {len(unscheduled)} tasks")

    # For first 10 unscheduled tasks, check why they can't schedule
    for task_id in unscheduled[:10]:
        task_info = scheduler.tasks[task_id]
        print(f"\nTask {task_id}:")
        print(f"  Type: {task_info['task_type']}")
        print(f"  Team needed: {task_info.get('team_skill', task_info.get('team'))}")
        print(f"  Product: {task_info.get('product')}")

        # Check dependencies
        waiting_for = []
        for c in constraints:
            if c['Second'] == task_id:
                if c['First'] not in scheduler.task_schedule:
                    waiting_for.append(c['First'])

        if waiting_for:
            print(f"  BLOCKED BY: {waiting_for[:5]}")
        else:
            print(f"  NOT BLOCKED (should be ready)")

        # Check team availability
        team = task_info.get('team_skill', task_info.get('team'))
        if team:
            capacity = scheduler.team_capacity.get(team, 0) or scheduler.quality_team_capacity.get(team, 0)
            print(f"  Team capacity: {capacity}")

def debug_unscheduled_tasks(scheduler):
    """Debug why tasks aren't being scheduled"""
    unscheduled = [t for t in scheduler.tasks if t not in scheduler.task_schedule]

    print(f"\n[DEBUG] {len(unscheduled)} unscheduled tasks")

    # Group by team
    by_team = defaultdict(list)
    for task_id in unscheduled[:20]:  # First 20
        task_info = scheduler.tasks[task_id]
        team = task_info.get('team_skill', task_info.get('team', 'NO_TEAM'))
        by_team[team].append(task_id)

    print("Sample unscheduled tasks by team:")
    for team, tasks in by_team.items():
        capacity = scheduler.team_capacity.get(team, 0) or scheduler.quality_team_capacity.get(team, 0)
        print(f"  {team}: {len(tasks)} tasks, capacity={capacity}")

        # Check if this team exists in capacity tables
        if team not in scheduler.team_capacity and team not in scheduler.quality_team_capacity:
            print(f"    WARNING: Team '{team}' not in capacity tables!")

def debug_scheduling_failure(scheduler, task_id):
    """Debug why a specific task cannot be scheduled"""
    print(f"\n" + "=" * 80)
    print(f"DEBUGGING: {task_id}")
    print("=" * 80)

    if task_id not in scheduler.tasks:
        print(f"❌ Task {task_id} does not exist!")
        return

    task_info = scheduler.tasks[task_id]
    print(f"\nTask Details:")
    print(f"  Type: {task_info['task_type']}")
    print(f"  Team: {task_info.get('team', 'NONE')}")
    print(f"  Mechanics Required: {task_info['mechanics_required']}")
    print(f"  Duration: {task_info['duration']} minutes")

    team = task_info.get('team')
    if team:
        capacity = scheduler.team_capacity.get(team, 0) or scheduler.quality_team_capacity.get(team, 0)
        print(f"\nTeam Capacity:")
        print(f"  {team}: {capacity} people")

        if task_info['mechanics_required'] > capacity:
            print(f"  ❌ IMPOSSIBLE: Task needs {task_info['mechanics_required']} but team has {capacity}")

def diagnose_scheduling_issues(scheduler):
    """Diagnose why tasks aren't being scheduled"""
    print("\n" + "=" * 80)
    print("SCHEDULING DIAGNOSTIC REPORT")
    print("=" * 80)

    # Count tasks by status
    total_tasks = len(scheduler.tasks)
    scheduled_tasks = len(scheduler.task_schedule)
    unscheduled_tasks = total_tasks - scheduled_tasks

    print(f"\nTask Scheduling Summary:")
    print(f"  Total tasks: {total_tasks}")
    print(f"  Scheduled: {scheduled_tasks}")
    print(f"  Unscheduled: {unscheduled_tasks}")

    # Identify unscheduled tasks
    unscheduled = []
    for task_id in scheduler.tasks:
        if task_id not in scheduler.task_schedule:
            unscheduled.append(task_id)

    # Analyze unscheduled tasks by type
    unscheduled_by_type = defaultdict(list)
    unscheduled_by_product = defaultdict(list)
    unscheduled_by_team = defaultdict(list)

    for task_id in unscheduled:
        task_info = scheduler.tasks[task_id]
        task_type = task_info.get('task_type', 'Unknown')
        product = task_info.get('product', 'Unknown')
        team = task_info.get('team', 'No Team')

        unscheduled_by_type[task_type].append(task_id)
        unscheduled_by_product[product].append(task_id)
        unscheduled_by_team[team].append(task_id)

    print("\n[UNSCHEDULED TASKS BY TYPE]")
    for task_type, task_list in sorted(unscheduled_by_type.items()):
        print(f"  {task_type}: {len(task_list)} tasks")
        # Show first few examples
        examples = task_list[:3]
        for ex in examples:
            task_info = scheduler.tasks[ex]
            print(f"    - {ex}: team={task_info.get('team', 'None')}, "
                  f"product={task_info.get('product', 'None')}")

    print("\n[UNSCHEDULED TASKS BY PRODUCT]")
    for product, task_list in sorted(unscheduled_by_product.items()):
        print(f"  {product}: {len(task_list)} tasks")

    print("\n[UNSCHEDULED TASKS BY TEAM]")
    for team, task_list in sorted(unscheduled_by_team.items()):
        print(f"  {team}: {len(task_list)} tasks")

    # Check for constraint issues
    print("\n[CONSTRAINT ANALYSIS]")
    dynamic_constraints = scheduler.build_dynamic_dependencies()

    # Find tasks with unsatisfied dependencies
    blocked_tasks = []
    for task_id in unscheduled:
        predecessors = []
        for constraint in dynamic_constraints:
            if constraint['Second'] == task_id:
                first_task = constraint['First']
                if first_task not in scheduler.task_schedule:
                    predecessors.append(first_task)

        if predecessors:
            blocked_tasks.append((task_id, predecessors))

    print(f"\nTasks blocked by unscheduled predecessors: {len(blocked_tasks)}")
    for task_id, preds in blocked_tasks[:5]:  # Show first 5
        print(f"  {task_id} blocked by: {preds[:3]}")  # Show first 3 blockers

    # Check for circular dependencies
    print("\n[CIRCULAR DEPENDENCY CHECK]")

    cycles = scheduler.find_dependency_cycles()
    if cycles:
        print(f"  Found {len(cycles)} cycles!")
        for i, cycle in enumerate(cycles[:3], 1):
            print(f"    Cycle {i}: {' -> '.join(cycle[:5])}")
    else:
        print("  No cycles detected")

    # Check for orphaned tasks (no incoming or outgoing dependencies)
    print("\n[ORPHANED TASKS CHECK]")
    tasks_in_constraints = set()
    for constraint in dynamic_constraints:
        tasks_in_constraints.add(constraint['First'])
        tasks_in_constraints.add(constraint['Second'])

    orphaned = []
    for task_id in scheduler.tasks:
        if task_id not in tasks_in_constraints:
            orphaned.append(task_id)

    print(f"  Tasks not in any constraints: {len(orphaned)}")
    for task_id in orphaned[:5]:
        task_info = scheduler.tasks[task_id]
        print(f"    - {task_id}: type={task_info.get('task_type')}, "
              f"product={task_info.get('product')}")

    # Check team availability
    print("\n[TEAM CAPACITY CHECK]")
    for team in sorted(set(scheduler.team_capacity.keys()) | set(scheduler.quality_team_capacity.keys())):
        capacity = scheduler.team_capacity.get(team, 0) or scheduler.quality_team_capacity.get(team, 0)

        # Count tasks needing this team
        tasks_needing_team = [t for t in scheduler.tasks if scheduler.tasks[t].get('team') == team]
        scheduled_for_team = [t for t in scheduler.task_schedule if scheduler.task_schedule[t].get('team') == team]

        print(f"  {team}:")
        print(f"    Capacity: {capacity}")
        print(f"    Total tasks needing team: {len(tasks_needing_team)}")
        print(f"    Scheduled: {len(scheduled_for_team)}")
        print(f"    Unscheduled: {len(tasks_needing_team) - len(scheduled_for_team)}")

    return {
        'total_tasks': total_tasks,
        'scheduled': scheduled_tasks,
        'unscheduled': unscheduled,
        'unscheduled_by_type': dict(unscheduled_by_type),
        'unscheduled_by_product': dict(unscheduled_by_product),
        'blocked_tasks': blocked_tasks,
        'cycles': cycles,
        'orphaned': orphaned
    }

def run_diagnostic(scheduler):
    """Run diagnostic after scheduling attempt"""
    print("\nRunning scheduling diagnostic...")

    # First, try to schedule with high verbosity
    scheduler.schedule_tasks(allow_late_delivery=True, silent_mode=False)

    # Then run the diagnostic
    diagnostic_results = diagnose_scheduling_issues(scheduler)

    # Additional specific checks
    print("\n[QUALITY INSPECTION MAPPING CHECK]")
    qi_without_team = 0
    qi_with_team = 0

    for task_id, task_info in scheduler.tasks.items():
        if task_info.get('is_quality', False):
            if task_info.get('team'):
                qi_with_team += 1
            else:
                qi_without_team += 1
                print(f"  QI without team: {task_id}")

    print(f"  Quality inspections with teams: {qi_with_team}")
    print(f"  Quality inspections without teams: {qi_without_team}")

    return diagnostic_results

def debug_scheduling_slot_search(scheduler, task_id, verbose=True):
    """Debug why a specific task cannot find a scheduling slot"""
    if task_id not in scheduler.tasks:
        print(f"Task {task_id} not found")
        return

    task_info = scheduler.tasks[task_id]
    duration = task_info['duration']
    mechanics_needed = task_info['mechanics_required']
    is_quality = task_info['is_quality']
    product = task_info.get('product')

    # Get team
    if is_quality:
        team = scheduler.map_mechanic_to_quality_team(task_info.get('team'))
        capacity = scheduler.quality_team_capacity.get(team, 0)
        shifts = scheduler.quality_team_shifts.get(team, [])
    else:
        team = task_info.get('team_skill', task_info.get('team'))
        capacity = scheduler.team_capacity.get(team, 0)
        shifts = scheduler.team_shifts.get(team, [])

    print(f"\n[SLOT DEBUG] Task: {task_id}")
    print(f"  Team: {team} (capacity: {capacity})")
    print(f"  Needs: {mechanics_needed} people for {duration} minutes")
    print(f"  Shifts: {shifts}")

    if mechanics_needed > capacity:
        print(f"  ❌ IMPOSSIBLE: Needs {mechanics_needed} but team only has {capacity}")
        return

    # Check first 3 days in detail
    current_time = datetime(2025, 8, 22, 6, 0)

    for day in range(3):
        test_date = current_time + timedelta(days=day)
        print(f"\n  Day {day + 1}: {test_date.date()}")

        if not scheduler.is_working_day(test_date, product):
            print(f"    Not a working day (holiday/weekend)")
            continue

        for shift in shifts:
            shift_info = scheduler.shift_hours.get(shift)
            if not shift_info:
                print(f"    Shift {shift}: No hours defined")
                continue

            print(f"    Shift {shift}: {shift_info['start']} - {shift_info['end']}")

            # Calculate actual shift window
            start_hour, start_min = scheduler._parse_shift_time(shift_info['start'])
            end_hour, end_min = scheduler._parse_shift_time(shift_info['end'])

            if shift == '3rd':
                shift_start = test_date.replace(hour=23, minute=0)
                shift_end = (test_date + timedelta(days=1)).replace(hour=6, minute=0)
            else:
                shift_start = test_date.replace(hour=start_hour, minute=start_min)
                shift_end = test_date.replace(hour=end_hour, minute=end_min)

            # Check capacity usage in this shift
            conflicts = 0
            conflicting_tasks = []

            for scheduled_id, schedule in scheduler.task_schedule.items():
                if schedule.get('team_skill', schedule.get('team')) == team:
                    # Check overlap
                    if schedule['start_time'] < shift_end and schedule['end_time'] > shift_start:
                        conflicts += schedule.get('mechanics_required', 1)
                        conflicting_tasks.append(scheduled_id)

            available = capacity - conflicts
            print(f"      Current usage: {conflicts}/{capacity}")

            if available >= mechanics_needed:
                print(f"      ✓ Could fit here (available: {available})")
            else:
                print(f"      ✗ Not enough capacity (available: {available})")
                if conflicting_tasks:
                    print(f"        Conflicts: {conflicting_tasks[:3]}")
