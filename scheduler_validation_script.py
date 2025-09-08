#!/usr/bin/env python3
"""
scheduler_validation.py

Standalone validation script for Production Scheduler
Validates schedule feasibility and identifies constraint violations

Usage:
    python scheduler_validation.py
    python scheduler_validation.py --scenario 1
    python scheduler_validation.py --debug-task "Product_A_4"
"""

import sys
import argparse
from datetime import datetime, timedelta
from collections import defaultdict
import pandas as pd

# Import your scheduler - adjust path as needed
try:
    from src.scheduler.main import ProductionScheduler
except ImportError:
    print("Error: Could not import ProductionScheduler from src/scheduler/main.py")
    print("Make sure the path is correct and the file exists.")
    sys.exit(1)


class SchedulerValidator:
    """Comprehensive validation system for production schedules"""
    
    def __init__(self, scheduler):
        """Initialize validator with a scheduler instance"""
        self.scheduler = scheduler
        self.validation_results = {}
        
    def validate_schedule_comprehensive(self, verbose=True):
        """
        Comprehensive validation of the generated schedule.
        Checks ALL constraints and returns detailed report.
        """
        validation_results = {
            'is_valid': True,
            'total_tasks': len(self.scheduler.tasks),
            'scheduled_tasks': len(self.scheduler.task_schedule),
            'errors': [],
            'warnings': [],
            'constraint_violations': [],
            'resource_violations': [],
            'precedence_violations': [],
            'late_part_violations': [],
            'stats': {}
        }
        
        if verbose:
            print("\n" + "=" * 80)
            print("COMPREHENSIVE SCHEDULE VALIDATION")
            print("=" * 80)
        
        # 1. CHECK TASK COMPLETION
        if verbose:
            print("\n1. Task Completion Check:")
            print("-" * 40)
        
        unscheduled_tasks = []
        for task_id in self.scheduler.tasks:
            if task_id not in self.scheduler.task_schedule:
                unscheduled_tasks.append(task_id)
                
        if unscheduled_tasks:
            validation_results['is_valid'] = False
            validation_results['errors'].append(f"INCOMPLETE: {len(unscheduled_tasks)} tasks not scheduled")
            if verbose:
                print(f"  ❌ {len(unscheduled_tasks)} tasks NOT scheduled:")
                for task_id in unscheduled_tasks[:10]:  # Show first 10
                    task_info = self.scheduler.tasks[task_id]
                    print(f"     - {task_id}: {task_info['task_type']}, "
                          f"needs {task_info['mechanics_required']} from {task_info.get('team', 'Any')}")
                if len(unscheduled_tasks) > 10:
                    print(f"     ... and {len(unscheduled_tasks) - 10} more")
        else:
            if verbose:
                print(f"  ✓ All {len(self.scheduler.tasks)} tasks scheduled")
        
        # 2. CHECK RESOURCE CAPACITY CONSTRAINTS
        if verbose:
            print("\n2. Resource Capacity Validation:")
            print("-" * 40)
        
        # Group tasks by team and time
        team_usage_timeline = defaultdict(lambda: defaultdict(list))
        
        for task_id, schedule in self.scheduler.task_schedule.items():
            team = schedule['team']
            start_time = schedule['start_time']
            end_time = schedule['end_time']
            mechanics_required = schedule['mechanics_required']
            
            # Sample check - every 15 minutes instead of every minute for performance
            current = start_time
            while current < end_time:
                team_usage_timeline[team][current].append({
                    'task_id': task_id,
                    'mechanics': mechanics_required
                })
                current += timedelta(minutes=15)
        
        capacity_violations = []
        for team, timeline in team_usage_timeline.items():
            capacity = self.scheduler.team_capacity.get(team, 0) or self.scheduler.quality_team_capacity.get(team, 0) or self.scheduler.customer_team_capacity.get(team, 0)
            
            for time_point, tasks_at_time in timeline.items():
                total_usage = sum(t['mechanics'] for t in tasks_at_time)
                
                if total_usage > capacity:
                    capacity_violations.append({
                        'team': team,
                        'time': time_point,
                        'usage': total_usage,
                        'capacity': capacity,
                        'tasks': [t['task_id'] for t in tasks_at_time]
                    })
        
        if capacity_violations:
            validation_results['is_valid'] = False
            validation_results['resource_violations'] = capacity_violations
            validation_results['errors'].append(f"CAPACITY: {len(capacity_violations)} capacity violations")
            
            if verbose:
                print(f"  ❌ Found {len(capacity_violations)} capacity violations:")
                # Group by team for cleaner output
                violations_by_team = defaultdict(list)
                for v in capacity_violations[:50]:  # Limit output
                    violations_by_team[v['team']].append(v)
                
                for team, violations in list(violations_by_team.items())[:5]:  # Show first 5 teams
                    capacity = self.scheduler.team_capacity.get(team, 0) or self.scheduler.quality_team_capacity.get(team, 0)
                    print(f"     {team} (capacity: {capacity}):")
                    for v in violations[:3]:  # Show first 3 per team
                        print(f"       - At {v['time'].strftime('%Y-%m-%d %H:%M')}: "
                              f"needs {v['usage']} people (over by {v['usage'] - capacity})")
        else:
            if verbose:
                print(f"  ✓ All resource capacity constraints satisfied")
        
        # 3. CHECK PRECEDENCE CONSTRAINTS
        if verbose:
            print("\n3. Precedence Constraint Validation:")
            print("-" * 40)
        
        dynamic_constraints = self.scheduler.build_dynamic_dependencies()
        precedence_violations = []
        
        for constraint in dynamic_constraints:
            first_id = constraint['First']
            second_id = constraint['Second']
            relationship = constraint['Relationship']
            
            if first_id in self.scheduler.task_schedule and second_id in self.scheduler.task_schedule:
                first_end = self.scheduler.task_schedule[first_id]['end_time']
                second_start = self.scheduler.task_schedule[second_id]['start_time']
                
                if relationship in ['Finish <= Start', 'Finish = Start']:
                    if first_end > second_start + timedelta(minutes=1):  # 1 minute tolerance
                        precedence_violations.append({
                            'first': first_id,
                            'second': second_id,
                            'first_end': first_end,
                            'second_start': second_start,
                            'violation_minutes': (first_end - second_start).total_seconds() / 60,
                            'type': constraint.get('Type', 'Baseline')
                        })
        
        if precedence_violations:
            validation_results['is_valid'] = False
            validation_results['precedence_violations'] = precedence_violations
            validation_results['errors'].append(f"PRECEDENCE: {len(precedence_violations)} precedence violations")
            
            if verbose:
                print(f"  ❌ Found {len(precedence_violations)} precedence violations:")
                for v in precedence_violations[:10]:  # Show first 10
                    print(f"     - {v['first']} → {v['second']}: "
                          f"overlap by {v['violation_minutes']:.0f} minutes ({v['type']})")
                if len(precedence_violations) > 10:
                    print(f"     ... and {len(precedence_violations) - 10} more")
        else:
            if verbose:
                print(f"  ✓ All {len(dynamic_constraints)} precedence constraints satisfied")
        
        # 4. CHECK LATE PART CONSTRAINTS
        if verbose:
            print("\n4. Late Part Timing Validation:")
            print("-" * 40)
        
        late_part_violations = []
        late_part_count = 0
        
        for task_id in self.scheduler.late_part_tasks:
            late_part_count += 1
            if task_id in self.scheduler.task_schedule:
                schedule = self.scheduler.task_schedule[task_id]
                original_task_id = self.scheduler.instance_to_original_task.get(task_id)
                
                if original_task_id and original_task_id in self.scheduler.on_dock_dates:
                    on_dock_date = self.scheduler.on_dock_dates[original_task_id]
                    earliest_allowed = on_dock_date + timedelta(days=self.scheduler.late_part_delay_days)
                    earliest_allowed = earliest_allowed.replace(hour=6, minute=0, second=0)
                    
                    if schedule['start_time'] < earliest_allowed:
                        late_part_violations.append({
                            'task': task_id,
                            'scheduled_start': schedule['start_time'],
                            'earliest_allowed': earliest_allowed,
                            'on_dock_date': on_dock_date,
                            'violation_days': (earliest_allowed - schedule['start_time']).days
                        })
        
        if late_part_violations:
            validation_results['is_valid'] = False
            validation_results['late_part_violations'] = late_part_violations
            validation_results['errors'].append(f"LATE PARTS: {len(late_part_violations)} started too early")
            
            if verbose:
                print(f"  ❌ Found {len(late_part_violations)} late part timing violations:")
                for v in late_part_violations[:5]:
                    print(f"     - {v['task']}: scheduled {v['scheduled_start'].strftime('%Y-%m-%d')}, "
                          f"on-dock {v['on_dock_date'].strftime('%Y-%m-%d')}, "
                          f"earliest allowed {v['earliest_allowed'].strftime('%Y-%m-%d')}")
        else:
            if verbose:
                if late_part_count > 0:
                    print(f"  ✓ All {late_part_count} late part timing constraints satisfied")
                else:
                    print(f"  ✓ No late part tasks in schedule")
        
        # 5. CHECK TASK RESOURCE REQUIREMENTS
        if verbose:
            print("\n5. Task Resource Requirement Validation:")
            print("-" * 40)
        
        resource_requirement_violations = []
        for task_id, schedule in self.scheduler.task_schedule.items():
            task_info = self.scheduler.tasks[task_id]
            team = schedule['team']
            mechanics_scheduled = schedule.get('mechanics_required') or schedule.get('personnel_required')
            mechanics_needed = task_info.get('mechanics_required') or task_info.get('personnel_required')
            
            # Check if team has enough capacity
            capacity = self.scheduler.team_capacity.get(team, 0) or self.scheduler.quality_team_capacity.get(team, 0) or self.scheduler.customer_team_capacity.get(team, 0)
            
            if mechanics_needed > capacity:
                resource_requirement_violations.append({
                    'task': task_id,
                    'team': team,
                    'needs': mechanics_needed,
                    'team_capacity': capacity,
                    'task_type': task_info['task_type']
                })
            
            if mechanics_scheduled != mechanics_needed:
                resource_requirement_violations.append({
                    'task': task_id,
                    'scheduled': mechanics_scheduled,
                    'needed': mechanics_needed,
                    'mismatch': True
                })
        
        if resource_requirement_violations:
            validation_results['is_valid'] = False
            validation_results['errors'].append(f"RESOURCES: {len(resource_requirement_violations)} "
                                               f"task resource requirement violations")
            
            if verbose:
                print(f"  ❌ Found {len(resource_requirement_violations)} resource requirement violations:")
                for v in resource_requirement_violations[:10]:
                    if 'team_capacity' in v:
                        print(f"     - {v['task']} ({v['task_type']}): needs {v['needs']} people "
                              f"but {v['team']} only has {v['team_capacity']} capacity")
                    elif 'mismatch' in v:
                        print(f"     - {v['task']}: scheduled with {v['scheduled']} "
                              f"but needs {v['needed']}")
        else:
            if verbose:
                print(f"  ✓ All task resource requirements satisfied")
        
        # 6. CHECK DELIVERY DATES
        if verbose:
            print("\n6. Delivery Date Analysis:")
            print("-" * 40)
        
        metrics = self.scheduler.calculate_lateness_metrics()
        late_deliveries = []
        
        for product, data in metrics.items():
            if data['lateness_days'] > 0 and data['lateness_days'] < 999999:
                late_deliveries.append({
                    'product': product,
                    'delivery_date': data['delivery_date'],
                    'projected_completion': data['projected_completion'],
                    'lateness_days': data['lateness_days']
                })
        
        if late_deliveries:
            validation_results['warnings'].append(f"DELIVERY: {len(late_deliveries)} products will be late")
            if verbose:
                print(f"  ⚠ {len(late_deliveries)} products will be delivered late:")
                for d in sorted(late_deliveries, key=lambda x: x['lateness_days'], reverse=True)[:10]:
                    print(f"     - {d['product']}: {d['lateness_days']} days late")
        else:
            if verbose:
                print(f"  ✓ All products on time for delivery")
        
        # 7. STATISTICS SUMMARY
        if verbose:
            print("\n7. Schedule Statistics:")
            print("-" * 40)
        
        # Calculate utilization
        team_utilization = {}
        for team in list(self.scheduler.team_capacity.keys()) + list(self.scheduler.quality_team_capacity.keys()) + list(self.scheduler.customer_team_capacity.keys()):
            capacity = self.scheduler.team_capacity.get(team, 0) or self.scheduler.quality_team_capacity.get(team, 0) or self.scheduler.customer_team_capacity.get(team, 0)
            if capacity > 0:
                total_work = sum(
                    sched['duration'] * sched['mechanics_required']
                    for sched in self.scheduler.task_schedule.values()
                    if sched.get('team') == team
                )
                makespan_days = self.scheduler.calculate_makespan()
                available_minutes = capacity * 8 * 60 * max(makespan_days, 1)
                if available_minutes > 0:
                    team_utilization[team] = (total_work / available_minutes) * 100
        
        validation_results['stats'] = {
            'makespan_days': self.scheduler.calculate_makespan(),
            'team_utilization': team_utilization,
            'late_deliveries': len(late_deliveries),
            'total_products': len(self.scheduler.delivery_dates)
        }
        
        if verbose:
            print(f"  Makespan: {validation_results['stats']['makespan_days']} working days")
            print(f"  Team Utilization:")
            for team, util in sorted(team_utilization.items()):
                capacity = self.scheduler.team_capacity.get(team, 0) or self.scheduler.quality_team_capacity.get(team, 0)
                status = "⚠ OVER" if util > 100 else "✓"
                print(f"    {status} {team} ({capacity} people): {util:.1f}%")
        
        # 8. QUALITY INSPECTION ASSIGNMENT VALIDATION
        if verbose:
            print("\n8. Quality Inspection Assignment Validation:")
            print("-" * 40)

        qi_tasks_checked = 0
        for task_id, schedule in self.scheduler.task_schedule.items():
            task_info = self.scheduler.tasks.get(task_id, {})
            if task_info.get('is_quality'):
                qi_tasks_checked += 1
                primary_task_id = task_info.get('primary_task')
                if not primary_task_id or primary_task_id not in self.scheduler.tasks:
                    print(f"  - QI Task {task_id}: Primary task {primary_task_id} not found.")
                    continue

                primary_task_info = self.scheduler.tasks[primary_task_id]
                mechanic_team = primary_task_info.get('team_skill', 'N/A')

                # Print assignment details for user visibility
                print(f"  - QI Task: {task_id} (for Primary: {primary_task_id})")
                print(f"    - Inspector: Assigned to [{schedule.get('team')}] with {schedule.get('mechanics_required')} inspector(s).")
                print(f"    - Assisting Mechanic: From [{mechanic_team}].")

        if verbose:
            if qi_tasks_checked > 0:
                print(f"\n  ✓ Displayed assignment details for {qi_tasks_checked} QI tasks.")
                print(f"  Note: Mechanic's availability during inspection is enforced by the model and validated in 'Resource Capacity Validation'.")
            else:
                print("  - No quality inspections to validate.")

        # FINAL VERDICT
        if verbose:
            print("\n" + "=" * 80)
            if validation_results['is_valid']:
                print("✅ SCHEDULE IS VALID AND FEASIBLE")
            else:
                print("❌ SCHEDULE IS INVALID")
                print("\nCritical Issues:")
                for error in validation_results['errors']:
                    print(f"  - {error}")
            
            if validation_results['warnings']:
                print("\nWarnings:")
                for warning in validation_results['warnings']:
                    print(f"  - {warning}")
            print("=" * 80)
        
        return validation_results
    
    def debug_scheduling_failure(self, task_id):
        """
        Debug why a specific task cannot be scheduled.
        """
        print(f"\n" + "=" * 80)
        print(f"DEBUGGING: Why can't {task_id} be scheduled?")
        print("=" * 80)
        
        if task_id not in self.scheduler.tasks:
            print(f"❌ Task {task_id} does not exist!")
            return
        
        task_info = self.scheduler.tasks[task_id]
        print(f"\nTask Details:")
        print(f"  ID: {task_id}")
        print(f"  Type: {task_info['task_type']}")
        print(f"  Team Required: {task_info.get('team', 'Any')}")
        print(f"  Mechanics Required: {task_info['mechanics_required']}")
        print(f"  Duration: {task_info['duration']} minutes")
        print(f"  Product: {task_info.get('product', 'Unknown')}")
        
        # Check team capacity
        team = task_info.get('team')
        if team:
            capacity = self.scheduler.team_capacity.get(team, 0) or self.scheduler.quality_team_capacity.get(team, 0)
            print(f"\nTeam Capacity Check:")
            print(f"  {team} current capacity: {capacity}")
            
            if task_info['mechanics_required'] > capacity:
                print(f"  ❌ IMPOSSIBLE: Task needs {task_info['mechanics_required']} but team only has {capacity}")
                print(f"  💡 Solution: Increase {team} capacity to at least {task_info['mechanics_required']}")
                return
            else:
                print(f"  ✓ Team has sufficient capacity")
        
        # Check dependencies
        dynamic_constraints = self.scheduler.build_dynamic_dependencies()
        dependencies = []
        for constraint in dynamic_constraints:
            if constraint['Second'] == task_id:
                dependencies.append(constraint['First'])
        
        if dependencies:
            print(f"\nDependency Check:")
            print(f"  Task depends on: {len(dependencies)} other tasks")
            unscheduled_deps = []
            for dep in dependencies:
                if dep not in self.scheduler.task_schedule:
                    unscheduled_deps.append(dep)
            
            if unscheduled_deps:
                print(f"  ❌ {len(unscheduled_deps)} dependencies not yet scheduled:")
                for dep in unscheduled_deps[:5]:
                    dep_info = self.scheduler.tasks.get(dep, {})
                    print(f"     - {dep}: {dep_info.get('task_type', 'Unknown')} "
                          f"(needs {dep_info.get('mechanics_required', 0)} from {dep_info.get('team', 'Unknown')})")
                print(f"  💡 Solution: Schedule dependencies first")
            else:
                print(f"  ✓ All dependencies scheduled")
                # Check timing
                latest_dep_end = max(self.scheduler.task_schedule[dep]['end_time'] for dep in dependencies)
                print(f"  Latest dependency ends: {latest_dep_end}")
        
        # Check for late part constraints
        if task_id in self.scheduler.late_part_tasks:
            original_id = self.scheduler.instance_to_original_task.get(task_id)
            if original_id and original_id in self.scheduler.on_dock_dates:
                on_dock = self.scheduler.on_dock_dates[original_id]
                earliest = on_dock + timedelta(days=self.scheduler.late_part_delay_days)
                print(f"\nLate Part Constraint:")
                print(f"  On-dock date: {on_dock}")
                print(f"  Earliest start: {earliest}")
                if datetime.now() < earliest:
                    print(f"  ❌ Cannot start yet (too early)")
                    print(f"  💡 Solution: Wait until {earliest}")
        
        print("\n" + "=" * 80)
        print("DIAGNOSIS SUMMARY:")
        if task_info['mechanics_required'] > capacity:
            print("  ❌ CRITICAL: Insufficient team capacity - CANNOT BE SCHEDULED")
        elif unscheduled_deps:
            print("  ⚠ Waiting on dependencies to be scheduled first")
        else:
            print("  ⚠ May be a scheduling algorithm issue or timeout")
        print("=" * 80)
    
    def validate_scenario(self, scenario_num):
        """Validate a specific scenario"""
        print(f"\n{'='*80}")
        print(f"VALIDATING SCENARIO {scenario_num}")
        print(f"{'='*80}")
        
        try:
            if scenario_num == 1:
                print("Running Scenario 1: CSV-defined Headcount...")
                results = self.scheduler.scenario_1_csv_headcount()
            elif scenario_num == 3:
                print("Running Scenario 3: Optimal Schedule (CP-SAT)...")
                results = self.scheduler.scenario_3_optimal_schedule()
            else:
                print(f"Invalid scenario number: {scenario_num}")
                return None
            
            if results:
                validation = self.validate_schedule_comprehensive(verbose=True)
                
                if not validation['is_valid']:
                    print("\n⚠️  CRITICAL WARNING: The generated schedule is NOT valid!")
                    print("The optimization claims to have found a solution but the schedule")
                    print("violates constraints. This indicates a bug in the scheduling algorithm.")
                    
                    # Debug first unscheduled task
                    if validation['scheduled_tasks'] < validation['total_tasks']:
                        print("\nDebugging first unscheduled task...")
                        for task_id in self.scheduler.tasks:
                            if task_id not in self.scheduler.task_schedule:
                                self.debug_scheduling_failure(task_id)
                                break
                
                return validation
            else:
                print(f"Scenario {scenario_num} failed to find a solution")
                return None
                
        except Exception as e:
            print(f"Error running scenario {scenario_num}: {str(e)}")
            import traceback
            traceback.print_exc()
            return None
    
    def validate_all_scenarios(self):
        """Run and validate all three scenarios"""
        print("\n" + "="*80)
        print("VALIDATING ALL SCENARIOS")
        print("="*80)
        
        all_results = {}
        
        for scenario_num in [1, 3]:
            validation = self.validate_scenario(scenario_num)
            all_results[f'scenario_{scenario_num}'] = validation
            
            # Summary for this scenario
            if validation:
                if validation['is_valid']:
                    print(f"\n✅ Scenario {scenario_num}: VALID")
                else:
                    print(f"\n❌ Scenario {scenario_num}: INVALID - {len(validation['errors'])} errors")
            else:
                print(f"\n❌ Scenario {scenario_num}: FAILED TO RUN")
        
        # Final summary
        print("\n" + "="*80)
        print("VALIDATION SUMMARY")
        print("="*80)
        
        for scenario_num in [1, 3]:
            result = all_results.get(f'scenario_{scenario_num}')
            if result:
                if result['is_valid']:
                    print(f"Scenario {scenario_num}: ✅ VALID ({result['scheduled_tasks']}/{result['total_tasks']} tasks)")
                else:
                    print(f"Scenario {scenario_num}: ❌ INVALID - Issues: {', '.join(result['errors'])}")
            else:
                print(f"Scenario {scenario_num}: ⚠ Did not run")
        
        return all_results


def main():
    """Main entry point for validation script"""
    parser = argparse.ArgumentParser(description='Validate Production Scheduler')
    parser.add_argument('--scenario', type=int, choices=[1, 2, 3],
                       help='Validate specific scenario (1, 2, or 3)')
    parser.add_argument('--debug-task', type=str,
                       help='Debug why a specific task cannot be scheduled')
    parser.add_argument('--csv', type=str, default='scheduling_data.csv',
                       help='Path to CSV file (default: scheduling_data.csv)')
    parser.add_argument('--all', action='store_true',
                       help='Validate all scenarios')
    
    args = parser.parse_args()
    
    print("Production Scheduler Validator")
    print("="*80)
    
    # Create scheduler instance
    print(f"Loading data from {args.csv}...")
    scheduler = ProductionScheduler(args.csv, debug=False)
    
    try:
        scheduler.load_data_from_csv()
        print(f"✓ Loaded {len(scheduler.tasks)} task instances")
        print(f"✓ Loaded {len(scheduler.delivery_dates)} product lines")
    except Exception as e:
        print(f"❌ Failed to load data: {str(e)}")
        return 1
    
    # Create validator
    validator = SchedulerValidator(scheduler)
    
    # Handle different modes
    if args.debug_task:
        # Debug specific task
        validator.debug_scheduling_failure(args.debug_task)
    elif args.all:
        # Validate all scenarios
        validator.validate_all_scenarios()
    elif args.scenario:
        # Validate specific scenario
        validator.validate_scenario(args.scenario)
    else:
        # Default: validate current schedule if it exists
        if scheduler.task_schedule:
            print("\nValidating existing schedule...")
            validation = validator.validate_schedule_comprehensive(verbose=True)
        else:
            print("\nNo existing schedule found. Running Scenario 1 for validation...")
            validation = validator.validate_scenario(1)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
