# src/scheduler/cp_sat_solver.py
# This file contains the new CP-SAT based scheduling algorithm.

from ortools.sat.python import cp_model
from datetime import datetime, timedelta
from collections import defaultdict

class CpSatScheduler:
    """
    A scheduler that uses Google's CP-SAT solver to find an optimal schedule.
    """
    def __init__(self, scheduler_instance):
        """
        Initializes the CpSatScheduler.
        Args:
            scheduler_instance: An instance of the main Scheduler class, containing all task and resource data.
        """
        self.scheduler = scheduler_instance
        self.model = cp_model.CpModel()
        self.task_vars = {}
        self.horizon = 0

    def _calculate_horizon(self):
        """
        Calculates a reasonable scheduling horizon.
        """
        start_date = self.scheduler.start_date
        latest_delivery = max(self.scheduler.delivery_dates.values())
        horizon_days = (latest_delivery - start_date).days + 90
        self.horizon = horizon_days * 24 * 60
        if self.scheduler.debug:
            print(f"[DEBUG] Calculated scheduling horizon: {self.horizon} minutes (approx. {horizon_days} days)")

    def _create_task_variables(self):
        """
        Creates the core CP-SAT variables (start, end, interval) for each task.
        """
        print("[INFO] Creating CP-SAT task variables...")
        for task_id, task_info in self.scheduler.tasks.items():
            duration = int(task_info['duration'])
            start_var = self.model.NewIntVar(0, self.horizon, f'{task_id}_start')
            end_var = self.model.NewIntVar(0, self.horizon, f'{task_id}_end')
            interval_var = self.model.NewIntervalVar(start_var, duration, end_var, f'{task_id}_interval')
            self.task_vars[task_id] = {'start': start_var, 'end': end_var, 'interval': interval_var, 'duration': duration}
        print(f"[INFO] Created {len(self.task_vars)} task variable sets.")

    def _add_precedence_constraints(self):
        """
        Adds precedence constraints to the model based on the dynamic dependency graph.
        """
        print("[INFO] Adding precedence constraints...")
        dependencies = self.scheduler.build_dynamic_dependencies()
        for const in dependencies:
            if const['First'] not in self.task_vars or const['Second'] not in self.task_vars:
                continue
            first_vars = self.task_vars[const['First']]
            second_vars = self.task_vars[const['Second']]
            relationship = const['Relationship']
            if relationship == 'Finish <= Start': self.model.Add(first_vars['end'] <= second_vars['start'])
            elif relationship == 'Finish = Start': self.model.Add(first_vars['end'] == second_vars['start'])
            elif relationship == 'Start <= Start': self.model.Add(first_vars['start'] <= second_vars['start'])
            elif relationship == 'Start = Start': self.model.Add(first_vars['start'] == second_vars['start'])
            elif relationship == 'Finish <= Finish': self.model.Add(first_vars['end'] <= second_vars['end'])
            else: self.model.Add(first_vars['end'] <= second_vars['start'])
        print(f"[INFO] Added {len(dependencies)} precedence constraints.")

        # Add constraints for late parts
        print("[INFO] Adding late part start time constraints...")
        late_part_constraints_added = 0
        for task_id, is_late in self.scheduler.late_part_tasks.items():
            if not is_late or task_id not in self.task_vars:
                continue

            original_task_id = self.scheduler.instance_to_original_task.get(task_id, task_id)
            on_dock_date = self.scheduler.on_dock_dates.get(original_task_id)

            if on_dock_date:
                earliest_start_dt = on_dock_date + timedelta(days=self.scheduler.late_part_delay_days)
                # Align with the start of the working day, as the validation script expects.
                earliest_start_dt = earliest_start_dt.replace(hour=6, minute=0, second=0, microsecond=0)
                earliest_start_minutes = int((earliest_start_dt - self.scheduler.start_date).total_seconds() / 60)

                # Task cannot start before the part is available
                self.model.Add(self.task_vars[task_id]['start'] >= earliest_start_minutes)
                late_part_constraints_added += 1
        print(f"[INFO] Added {late_part_constraints_added} late part timing constraints.")

    def _add_resource_constraints(self):
        """
        Adds resource constraints with corrected logic for Quality Inspections.
        This version fixes a double-counting bug and correctly models resource usage.
        """
        print("[INFO] Adding resource constraints with corrected shared mechanic logic...")
        resource_to_tasks = defaultdict(lambda: {'intervals': [], 'demands': []})

        for task_id, task_info in self.scheduler.tasks.items():
            task_vars = self.task_vars[task_id]

            if task_info.get('is_quality', False):
                # A Quality Inspection task consumes TWO resources simultaneously:
                # 1. A Quality Inspector from the designated Quality Team.
                quality_team = task_info.get('team')
                if quality_team:
                    # The 'mechanics_required' for a QI task is the number of QI personnel.
                    resource_to_tasks[quality_team]['intervals'].append(task_vars['interval'])
                    resource_to_tasks[quality_team]['demands'].append(task_info['mechanics_required'])

                # 2. A Mechanic from the primary task's team.
                primary_task_id = task_info.get('primary_task')
                if primary_task_id and primary_task_id in self.scheduler.tasks:
                    primary_task_info = self.scheduler.tasks[primary_task_id]
                    mechanic_team_resource = primary_task_info.get('team_skill')
                    if mechanic_team_resource:
                        # The number of mechanics required for the inspection is also in the QI task's data.
                        mechanic_headcount_for_qi = task_info.get('mechanics_required', 1)
                        resource_to_tasks[mechanic_team_resource]['intervals'].append(task_vars['interval'])
                        resource_to_tasks[mechanic_team_resource]['demands'].append(mechanic_headcount_for_qi)

            elif task_info.get('is_customer', False):
                # A Customer Inspection task consumes only a Customer resource.
                customer_team = task_info.get('team')
                if customer_team:
                    resource_to_tasks[customer_team]['intervals'].append(task_vars['interval'])
                    resource_to_tasks[customer_team]['demands'].append(task_info['mechanics_required'])

            else:
                # This is a standard Production, Rework, or Late Part task.
                # It only consumes a Mechanic resource.
                mechanic_team_resource = task_info.get('team_skill')
                if mechanic_team_resource:
                    resource_to_tasks[mechanic_team_resource]['intervals'].append(task_vars['interval'])
                    resource_to_tasks[mechanic_team_resource]['demands'].append(task_info['mechanics_required'])

        # Add all the cumulative constraints to the model
        all_resources = {**self.scheduler.team_capacity, **self.scheduler.quality_team_capacity, **self.scheduler.customer_team_capacity}
        for resource_name, capacity in all_resources.items():
            if resource_name in resource_to_tasks and capacity > 0:
                intervals = resource_to_tasks[resource_name]['intervals']
                demands = resource_to_tasks[resource_name]['demands']
                self.model.AddCumulative(intervals, demands, capacity)
                if self.scheduler.debug:
                    print(f"  - Added cumulative constraint for '{resource_name}' with capacity {capacity} and {len(intervals)} tasks.")

        print(f"[INFO] Added cumulative constraints for {len(resource_to_tasks)} unique resources.")

    def _add_shift_constraints(self):
        """
        Adds constraints to ensure tasks are only scheduled during a team's working shifts.
        This is a more robust implementation that correctly handles overnight shifts.
        """
        print("[INFO] Adding shift constraints...")
        s = self.scheduler
        minutes_in_day = 24 * 60

        for task_id, task_info in s.tasks.items():
            task_vars = self.task_vars[task_id]
            start_var = task_vars['start']
            duration = task_vars['duration']

            # Determine the team's shifts
            if task_info.get('is_quality'):
                team_shifts = s.quality_team_shifts.get(task_info['team'], [])
            elif task_info.get('is_customer'):
                team_shifts = s.customer_team_shifts.get(task_info['team'], [])
            else:
                team_key = task_info.get('team_skill', task_info.get('team'))
                base_team = team_key.split(' (')[0] if '(' in team_key else team_key
                team_shifts = s.team_shifts.get(base_team, [])

            if not team_shifts:
                if s.debug:
                    print(f"[WARNING] Task {task_id} belongs to a team with no shifts defined. Assuming 24/7 availability.")
                continue

            # Create a boolean variable for each shift the task could be in
            shift_literals = []
            for shift_name in team_shifts:
                shift_info = s.shift_hours.get(shift_name)
                if not shift_info: continue

                literal = self.model.NewBoolVar(f'{task_id}_in_{shift_name}')
                shift_literals.append(literal)

                # Define shift start/end times in minutes from midnight
                start_h, start_m = s._parse_shift_time(shift_info['start'])
                end_h, end_m = s._parse_shift_time(shift_info['end'])
                shift_start_min = start_h * 60 + start_m
                shift_end_min = end_h * 60 + end_m

                # Model task's start time relative to the day
                time_of_day = self.model.NewIntVar(0, minutes_in_day - 1, f'{task_id}_time_of_day_{shift_name}')
                self.model.AddModuloEquality(time_of_day, start_var, minutes_in_day)

                # Constrain the task to be within the shift if this literal is true
                if shift_start_min < shift_end_min:  # Normal day shift
                    # Task must start and end within the shift's daily window
                    self.model.Add(time_of_day >= shift_start_min).OnlyEnforceIf(literal)
                    self.model.Add(time_of_day + duration <= shift_end_min).OnlyEnforceIf(literal)
                else:  # Overnight shift
                    # For overnight shifts, the task can't start in the dead zone
                    # e.g., for 23:00-06:00, dead zone is 06:00-23:00
                    # So, time_of_day must be OUTSIDE [shift_end_min, shift_start_min)
                    is_after_start = self.model.NewBoolVar(f'{task_id}_{shift_name}_after_start')
                    is_before_end = self.model.NewBoolVar(f'{task_id}_{shift_name}_before_end')

                    self.model.Add(time_of_day >= shift_start_min).OnlyEnforceIf(is_after_start)
                    self.model.Add(time_of_day < shift_start_min).OnlyEnforceIf(is_after_start.Not())

                    self.model.Add(time_of_day < shift_end_min).OnlyEnforceIf(is_before_end)
                    self.model.Add(time_of_day >= shift_end_min).OnlyEnforceIf(is_before_end.Not())

                    # The start time must be in the valid range
                    self.model.AddBoolOr([is_after_start, is_before_end]).OnlyEnforceIf(literal)

                    # Additionally, ensure the task does not cross the dead zone
                    # If it starts before midnight, it must end before the dead zone starts (e.g., 06:00)
                    self.model.Add(time_of_day + duration <= shift_end_min + minutes_in_day).OnlyEnforceIf(literal, is_after_start)
                    # If it starts after midnight, it must end before the dead zone starts
                    self.model.Add(time_of_day + duration <= shift_end_min).OnlyEnforceIf(literal, is_before_end)


            # A task must be assigned to exactly one of its possible shifts
            if shift_literals:
                self.model.Add(sum(shift_literals) == 1)

        print(f"[INFO] Added shift constraints for all applicable tasks.")

    def _set_objective(self):
        """
        Defines the optimization objective for the model to minimize total lateness.
        """
        print("[INFO] Setting optimization objective (minimize total lateness)...")
        dependencies = self.scheduler.build_dynamic_dependencies()
        predecessor_tasks = {const['First'] for const in dependencies}
        all_lateness_vars = []
        start_datetime = self.scheduler.start_date
        for product, delivery_date in self.scheduler.delivery_dates.items():
            product_task_ids = [tid for tid, info in self.scheduler.tasks.items() if info.get('product') == product]
            if not product_task_ids: continue
            terminal_tasks = [tid for tid in product_task_ids if tid not in predecessor_tasks]
            if not terminal_tasks:
                last_task_id = max(product_task_ids, key=lambda tid: int(self.scheduler.instance_to_original_task.get(tid, 0)) if str(self.scheduler.instance_to_original_task.get(tid, 0)).isdigit() else 0)
                terminal_tasks = [last_task_id] if last_task_id else []
            if not terminal_tasks: continue
            product_makespan = self.model.NewIntVar(0, self.horizon, f'{product}_makespan')
            for task_id in terminal_tasks: self.model.Add(self.task_vars[task_id]['end'] <= product_makespan)
            delivery_deadline_minutes = int((delivery_date - start_datetime).total_seconds() / 60)
            lateness_var = self.model.NewIntVar(0, self.horizon, f'{product}_lateness')
            self.model.Add(product_makespan - delivery_deadline_minutes <= lateness_var)
            all_lateness_vars.append(lateness_var)
        if all_lateness_vars: self.model.Minimize(sum(all_lateness_vars))
        print(f"[INFO] Objective set to minimize the sum of {len(all_lateness_vars)} product lateness variables.")

    def solve(self):
        """
        This is the main method that will build and solve the CP-SAT model.
        """
        self._calculate_horizon()
        self._create_task_variables()
        self._add_precedence_constraints()
        self._add_resource_constraints()
        self._add_shift_constraints()
        self._set_objective()
        print("[INFO] Starting CP-SAT solver...")
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 180.0
        solver.parameters.log_search_progress = self.scheduler.debug
        status = solver.Solve(self.model)
        if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
            print(f"[INFO] Solver finished with status: {solver.StatusName(status)}")
            print(f"[INFO] Objective value (total lateness in minutes): {solver.ObjectiveValue()}")
            return self._extract_solution(solver)
        else:
            print(f"[ERROR] No solution found. Status: {solver.StatusName(status)}")
            return None

    def _extract_solution(self, solver):
        """
        Extracts the schedule from the solver and formats it.
        """
        print("[INFO] Extracting solution from solver...")
        schedule = {}
        start_datetime = self.scheduler.start_date
        for task_id, task_info in self.scheduler.tasks.items():
            start_minutes = solver.Value(self.task_vars[task_id]['start'])
            end_minutes = solver.Value(self.task_vars[task_id]['end'])
            schedule[task_id] = {
                'start_time': start_datetime + timedelta(minutes=start_minutes),
                'end_time': start_datetime + timedelta(minutes=end_minutes),
                'team': task_info.get('team'), 'team_skill': task_info.get('team_skill'),
                'skill': task_info.get('skill'), 'product': task_info.get('product'),
                'duration': task_info.get('duration'), 'mechanics_required': task_info.get('mechanics_required'),
                'is_quality': task_info.get('is_quality', False), 'is_customer': task_info.get('is_customer', False),
                'task_type': task_info.get('task_type'), 'original_task_id': task_info.get('original_task_id')
            }
        print(f"[INFO] Extracted schedule for {len(schedule)} tasks.")
        return schedule
