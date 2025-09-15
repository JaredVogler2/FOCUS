# src/scheduler/validation.py

from collections import defaultdict

def validate_dag(scheduler):
    """Validate that the dependency graph is a DAG"""
    print("\nValidating task dependency graph...")

    dynamic_constraints = scheduler.build_dynamic_dependencies()

    graph = defaultdict(set)
    all_tasks_in_constraints = set()

    for constraint in dynamic_constraints:
        first = constraint['First']
        second = constraint['Second']
        if constraint['Relationship'] in ['Finish <= Start', 'Finish = Start']:
            graph[first].add(second)
        all_tasks_in_constraints.add(first)
        all_tasks_in_constraints.add(second)

    missing_tasks = all_tasks_in_constraints - set(scheduler.tasks.keys())
    if missing_tasks:
        print(f"ERROR: Tasks in constraints but not defined: {missing_tasks}")
        return False

    def has_cycle_dfs(node, visited, rec_stack, path):
        visited.add(node)
        rec_stack.add(node)
        path.append(node)

        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                if has_cycle_dfs(neighbor, visited, rec_stack, path):
                    return True
            elif neighbor in rec_stack:
                cycle_start = path.index(neighbor)
                cycle = path[cycle_start:] + [neighbor]
                print(f"ERROR: Cycle detected: {' -> '.join(map(str, cycle))}")
                return True

        path.pop()
        rec_stack.remove(node)
        return False

    visited = set()
    for node in all_tasks_in_constraints:
        if node not in visited:
            if has_cycle_dfs(node, visited, set(), []):
                return False

    print(f"✓ DAG validation successful!")
    return True

def check_resource_conflicts(scheduler):
    """Check for resource conflicts"""
    conflicts = []
    if not scheduler.task_schedule:
        return conflicts

    team_tasks = defaultdict(list)
    for task_id, schedule in scheduler.task_schedule.items():
        team = schedule.get('team_skill', schedule.get('team'))
        if team:
            team_tasks[team].append((task_id, schedule))

    for team, tasks in team_tasks.items():
        capacity = scheduler.team_capacity.get(team, 0) or scheduler.quality_team_capacity.get(team, 0) or scheduler.customer_team_capacity.get(team, 0)

        events = []
        for task_id, schedule in tasks:
            events.append((schedule['start_time'], schedule['mechanics_required'], 'start', task_id))
            events.append((schedule['end_time'], -schedule['mechanics_required'], 'end', task_id))

        events.sort(key=lambda x: (x[0], x[1]))

        current_usage = 0
        for time, delta, event_type, task_id in events:
            if event_type == 'start':
                current_usage += delta
                if current_usage > capacity:
                    conflicts.append({
                        'team': team,
                        'time': time,
                        'usage': current_usage,
                        'capacity': capacity,
                        'task': task_id
                    })
            else:
                current_usage += delta

    return conflicts

def validate_schedule_comprehensive(scheduler, verbose=True):
    """Comprehensive validation of the generated schedule"""
    validation_results = {
        'is_valid': True,
        'total_tasks': len(scheduler.tasks),
        'scheduled_tasks': len(scheduler.task_schedule),
        'errors': [],
        'warnings': [],
        'stats': {}
    }

    if verbose:
        print("\n" + "=" * 80)
        print("SCHEDULE VALIDATION")
        print("=" * 80)

    unscheduled_tasks = []
    for task_id in scheduler.tasks:
        if task_id not in scheduler.task_schedule:
            unscheduled_tasks.append(task_id)

    if unscheduled_tasks:
        validation_results['is_valid'] = False
        validation_results['errors'].append(f"INCOMPLETE: {len(unscheduled_tasks)} tasks not scheduled")
        if verbose:
            print(f"\n❌ {len(unscheduled_tasks)} tasks NOT scheduled")
    else:
        if verbose:
            print(f"\n✓ All {len(scheduler.tasks)} tasks scheduled")

    return validation_results

def find_dependency_cycles(scheduler):
    """Find circular dependencies in the task graph"""
    graph = defaultdict(set)
    dynamic_constraints = scheduler.build_dynamic_dependencies()

    for constraint in dynamic_constraints:
        if constraint['Relationship'] in ['Finish <= Start', 'Finish = Start']:
            graph[constraint['First']].add(constraint['Second'])

    cycles = []
    visited = set()
    rec_stack = set()

    def dfs(node, path):
        visited.add(node)
        rec_stack.add(node)
        path.append(node)

        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                if dfs(neighbor, path):
                    return True
            elif neighbor in rec_stack:
                cycle_start = path.index(neighbor)
                cycle = path[cycle_start:] + [neighbor]
                cycles.append(cycle)
                return True

        path.pop()
        rec_stack.remove(node)
        return False

    for node in list(graph.keys()):
        if node not in visited:
            dfs(node, [])

    return cycles

def validate_schedulability(scheduler):
    """Validate that all tasks CAN theoretically be scheduled"""
    print("\n" + "=" * 80)
    print("SCHEDULABILITY VALIDATION")
    print("=" * 80)

    issues = []
    warnings = []

    # Check 1: Team capacity vs task requirements
    for task_id, task_info in scheduler.tasks.items():
        team = task_info.get('team_skill', task_info.get('team'))
        mechanics_needed = task_info.get('mechanics_required', 1)

        if task_info.get('is_quality'):
            capacity = scheduler.quality_team_capacity.get(team, 0)
        else:
            capacity = scheduler.team_capacity.get(team, 0)

        if capacity == 0:
            issues.append(f"Task {task_id} requires team '{team}' which has 0 capacity")
        elif mechanics_needed > capacity:
            issues.append(f"Task {task_id} needs {mechanics_needed} people but '{team}' only has {capacity}")

    # Check 2: Circular dependencies
    cycles = find_dependency_cycles(scheduler)
    if cycles:
        for cycle in cycles:
            issues.append(f"Circular dependency: {' -> '.join(cycle)}")

    # Check 3: Total workload vs theoretical capacity
    total_work_minutes = sum(
        task['duration'] * task.get('mechanics_required', 1)
        for task in scheduler.tasks.values()
    )

    # Calculate total available minutes over reasonable timeframe (30 days)
    total_capacity_minutes = 0
    for team, capacity in scheduler.team_capacity.items():
        if capacity > 0:
            total_capacity_minutes += capacity * 8 * 60 * 30  # 30 days
    for team, capacity in scheduler.quality_team_capacity.items():
        if capacity > 0:
            total_capacity_minutes += capacity * 8 * 60 * 30

    if total_work_minutes > total_capacity_minutes:
        issues.append(
            f"Total work ({total_work_minutes} min) exceeds 30-day capacity ({total_capacity_minutes} min)")

    # Check 4: Tasks with missing team assignments
    for task_id, task_info in scheduler.tasks.items():
        if not task_info.get('team') and not task_info.get('team_skill'):
            warnings.append(f"Task {task_id} has no team assignment")

    # Report results
    if issues:
        print(f"❌ Found {len(issues)} BLOCKING issues:")
        for issue in issues[:10]:  # Show first 10
            print(f"  - {issue}")
        return False
    elif warnings:
        print(f"⚠️ Found {len(warnings)} warnings:")
        for warning in warnings[:5]:
            print(f"  - {warning}")
        return True
    else:
        print("✓ All tasks can theoretically be scheduled")
        return True
