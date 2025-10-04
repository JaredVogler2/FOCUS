# src/scheduler/main.py

from collections import defaultdict
from datetime import datetime
import re
from . import data_loader, scenarios, metrics, utils, algorithms, validation, reporting, constraints, cp_sat_solver

class ProductionScheduler:
    """
    Production scheduling system orchestrator.
    This class holds the state of the scheduler and delegates operations
    to the various specialized modules.
    """

    def __init__(self, csv_file_path='scheduling_data.csv', debug=False, late_part_delay_days=1.0):
        """Initialize scheduler with all its data structures."""
        self.csv_file_path = utils.resource_path(csv_file_path)
        self.debug = debug
        self.late_part_delay_days = late_part_delay_days
        self.start_date = datetime(2025, 8, 22, 6, 0)

        # Data structures to hold scheduler state
        self.tasks = {}
        self.baseline_task_data = {}
        self.task_instance_map = {}
        self.instance_to_product = {}
        self.instance_to_original_task = {}
        self.quality_inspections = {}
        self.quality_requirements = {}
        self.customer_inspections = {}
        self.customer_requirements = {}
        self.customer_team_capacity = {}
        self.customer_team_shifts = {}
        self.precedence_constraints = []
        self.late_part_constraints = []
        self.rework_constraints = []
        self.product_remaining_ranges = {}
        self.late_part_tasks = {}
        self.rework_tasks = {}
        self.on_dock_dates = {}
        self.team_shifts = {}
        self.team_capacity = {}
        self.quality_team_shifts = {}
        self.quality_team_capacity = {}
        self.shift_hours = {}
        self.delivery_dates = {}
        self.holidays = defaultdict(set)

        # Scheduling results and caches
        self.task_schedule = {}
        self.global_priority_list = []
        self._dynamic_constraints_cache = None
        self._critical_path_cache = {}

        # Original capacities for resets
        self._original_team_capacity = {}
        self._original_quality_capacity = {}
        self._original_customer_team_capacity = {}

        self._next_instance_id = 1

    def calculate_minimum_team_requirements(self):
        """Calculate the minimum required capacity for each team based on task requirements"""
        min_requirements = {}

        # Initialize with all teams from capacity tables
        for team in self.team_capacity:
            min_requirements[team] = 0
        for team in self.quality_team_capacity:
            min_requirements[team] = 0

        # Check all tasks for their team_skill requirements
        for task_id, task_info in self.tasks.items():
            # Use team_skill if available, otherwise team
            team = task_info.get('team_skill', task_info.get('team'))
            mechanics_required = task_info.get('mechanics_required', 0)

            if team:
                if team in min_requirements:
                    min_requirements[team] = max(min_requirements[team], mechanics_required)
                else:
                    # Team not in capacity tables - this is a problem
                    if self.debug:
                        print(f"[WARNING] Task {task_id} requires team {team} not in capacity tables")

        # Check quality inspections
        for qi_id, qi_info in self.quality_inspections.items():
            headcount = qi_info.get('headcount', 0)
            # QI tasks should have their team assigned during loading
            if qi_id in self.tasks:
                team = self.tasks[qi_id].get('team')
                if team and team in min_requirements:
                    min_requirements[team] = max(min_requirements[team], headcount)

        return min_requirements

    # --- Method Delegation ---

    def load_data_from_csv(self):
        data_loader.load_data_from_csv(self)

    def generate_global_priority_list(self, allow_late_delivery=True, silent_mode=False):
        # algorithms.schedule_tasks(self, allow_late_delivery=allow_late_delivery, silent_mode=silent_mode)
        print("\n[INFO] Instantiating and running CP-SAT solver...")
        cp_scheduler = cp_sat_solver.CpSatScheduler(self)
        new_schedule = cp_scheduler.solve()

        if new_schedule:
            self.task_schedule = new_schedule
            print("[INFO] CP-SAT solver returned a valid schedule.")
        else:
            print("[ERROR] CP-SAT solver failed to find a solution. No schedule was generated.")
            # Clear the schedule to indicate failure
            self.task_schedule = {}

        conflicts = validation.check_resource_conflicts(self)
        if conflicts and not silent_mode:
            print(f"\n[WARNING] Found {len(conflicts)} resource conflicts")

        priority_data = []
        for task_instance_id, schedule in self.task_schedule.items():
            # If the task was split, recover the original instance ID for metrics and lookups
            original_instance_id = task_instance_id.split('---part')[0]

            slack = metrics.calculate_slack_time(self, original_instance_id)
            task_type = schedule['task_type']
            original_task_id = schedule.get('original_task_id')
            product = schedule.get('product', 'Unknown')
            criticality = algorithms.classify_task_criticality(self, original_instance_id)

            # Adjust display name for split parts
            is_split_part = schedule.get('is_split_part', False)
            part_num_match = re.search(r'---part(\d+)$', task_instance_id)
            part_num = part_num_match.group(1) if part_num_match else ''

            if task_type == 'Quality Inspection':
                primary_task = self.quality_inspections.get(original_instance_id, {}).get('primary_task')
                if primary_task:
                    primary_original = self.instance_to_original_task.get(primary_task, primary_task)
                    base_name = f"{product} QI for Task {primary_original}"
                else:
                    base_name = f"{product} QI {original_task_id}"
            elif task_type == 'Late Part':
                base_name = f"{product} Late Part {original_task_id}"
            elif task_type == 'Rework':
                base_name = f"{product} Rework {original_task_id}"
            else:
                base_name = f"{product} Task {original_task_id}"

            display_name = f"{base_name} (Part {part_num})" if is_split_part and part_num else base_name

            criticality_symbol = {'CRITICAL': '🔴', 'BUFFER': '🟡', 'FLEXIBLE': '🟢'}.get(criticality, '')
            display_name_with_criticality = f"{criticality_symbol} {display_name} [{criticality}]"

            priority_data.append({
                'task_instance_id': task_instance_id, 'task_type': task_type,
                'display_name': display_name, 'display_name_with_criticality': display_name_with_criticality,
                'criticality': criticality, 'product_line': product, 'original_task_id': original_task_id,
                'team': schedule.get('team'), 'scheduled_start': schedule.get('start_time'),
                'scheduled_end': schedule.get('end_time'), 'duration_minutes': schedule.get('duration'),
                'mechanics_required': schedule.get('mechanics_required'), 'slack_hours': slack,
                'slack_days': slack / 24, 'priority_score': algorithms.calculate_task_priority(self, task_instance_id),
                'shift': schedule.get('shift', 'N/A')  # Use .get() for safety
            })

        priority_data.sort(key=lambda x: (x['scheduled_start'], x['slack_hours']))
        for i, task in enumerate(priority_data, 1):
            task['global_priority'] = i

        self.global_priority_list = priority_data
        return priority_data

    def build_dynamic_dependencies(self):
        return constraints.build_dynamic_dependencies(self)

    def get_successors(self, task_id):
        return constraints.get_successors(self, task_id)

    def get_predecessors(self, task_id):
        return constraints.get_predecessors(self, task_id)

    def get_all_successors(self, start_task_id):
        """
        Get all unique successors for a given task by traversing the dependency graph.
        """
        # Set to keep track of visited nodes to avoid cycles and redundant work
        visited = set()
        # Queue for BFS traversal, initialized with the starting task
        queue = [start_task_id]
        # Set to store all unique successors found
        all_successors = set()

        while queue:
            current_task = queue.pop(0)
            if current_task in visited:
                continue
            visited.add(current_task)

            # Get direct successors for the current task
            direct_successors = self.get_successors(current_task)

            for successor in direct_successors:
                if successor not in all_successors:
                    all_successors.add(successor)
                    if successor not in visited:
                        queue.append(successor)

        return list(all_successors)

    def is_working_day(self, date, product_line):
        return utils.is_working_day(self, date, product_line)

    def print_delivery_analysis(self, scenario_name=""):
        return reporting.print_delivery_analysis(self, scenario_name)

    def scenario_1_csv_headcount(self):
        return scenarios.scenario_1_csv_headcount(self)

    def scenario_3_optimal_schedule(self):
        return scenarios.scenario_3_optimal_schedule(self)

    def validate_dag(self):
        return validation.validate_dag(self)

    def run_diagnostic(self):
        return debug.run_diagnostic(self)

    def map_mechanic_to_quality_team(self, mechanic_team):
        return data_loader.map_mechanic_to_quality_team(self, mechanic_team)

    def _parse_shift_time(self, time_str):
        return utils.parse_shift_time(time_str)

    def calculate_lateness_metrics(self):
        return metrics.calculate_lateness_metrics(self)

    def calculate_makespan(self):
        return metrics.calculate_makespan(self)
