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
        Adds resource and shift constraints. Non-working hours are modeled as dummy tasks
        that consume the full capacity of a resource.
        """
        print("[INFO] Adding resource and shift constraints...")
        s = self.scheduler
        minutes_in_day = 24 * 60
        resource_to_tasks = defaultdict(lambda: {'intervals': [], 'demands': []})

        # Add real tasks to the resource usage map
        for task_id, task_info in s.tasks.items():
            task_vars = self.task_vars[task_id]
            resource_key = None
            if task_info.get('is_quality'):
                resource_key = task_info.get('team')
            elif task_info.get('is_customer'):
                resource_key = task_info.get('team')
            else:
                resource_key = task_info.get('team_skill')

            if resource_key:
                resource_to_tasks[resource_key]['intervals'].append(task_vars['interval'])
                resource_to_tasks[resource_key]['demands'].append(task_info['mechanics_required'])

            # Handle shared mechanic for QI tasks
            if task_info.get('is_quality'):
                primary_task_id = task_info.get('primary_task')
                if primary_task_id and primary_task_id in s.tasks:
                    primary_task_info = s.tasks[primary_task_id]
                    mechanic_resource = primary_task_info.get('team_skill')
                    if mechanic_resource:
                        mechanic_headcount = task_info.get('mechanics_required', 1)
                        resource_to_tasks[mechanic_resource]['intervals'].append(task_vars['interval'])
                        resource_to_tasks[mechanic_resource]['demands'].append(mechanic_headcount)

        all_resources = {**s.team_capacity, **s.quality_team_capacity, **s.customer_team_capacity}

        # Add dummy tasks for non-working hours
        for resource, capacity in all_resources.items():
            if capacity <= 0: continue

            team_type = 'mechanic'
            if 'Quality' in resource: team_type = 'quality'
            elif 'Customer' in resource: team_type = 'customer'

            base_team = resource.split(' (')[0]

            shifts = []
            if team_type == 'quality':
                shifts = s.quality_team_shifts.get(base_team, [])
            elif team_type == 'customer':
                shifts = s.customer_team_shifts.get(base_team, [])
            else: # mechanic
                shifts = s.team_shifts.get(base_team, [])

            if not shifts:
                if s.debug: print(f"[DEBUG] Resource {resource} has no defined shifts, assuming 24/7.")
                continue

            # Calculate working intervals for one day
            working_intervals = []
            for shift_name in shifts:
                shift_info = s.shift_hours.get(shift_name)
                if shift_info:
                    start_h, start_m = s._parse_shift_time(shift_info['start'])
                    end_h, end_m = s._parse_shift_time(shift_info['end'])
                    working_intervals.append((start_h * 60 + start_m, end_h * 60 + end_m))

            working_intervals.sort()

            # Calculate non-working intervals
            non_working_intervals = []
            last_end = 0
            for start, end in working_intervals:
                if start > last_end:
                    non_working_intervals.append((last_end, start))
                last_end = end
            if last_end < minutes_in_day:
                non_working_intervals.append((last_end, minutes_in_day))

            # Create dummy tasks for non-working intervals for each day of the horizon
            for day in range(int(self.horizon / minutes_in_day)):
                for start, end in non_working_intervals:
                    if start == end: continue

                    dummy_start = day * minutes_in_day + start
                    dummy_duration = end - start
                    dummy_end = dummy_start + dummy_duration

                    dummy_interval = self.model.NewIntervalVar(
                        dummy_start, dummy_duration, dummy_end, f'dummy_{resource}_day{day}_{start}'
                    )
                    resource_to_tasks[resource]['intervals'].append(dummy_interval)
                    resource_to_tasks[resource]['demands'].append(capacity + 1) # Block the resource completely

        # Add cumulative constraints for all resources
        for resource_name, usage_data in resource_to_tasks.items():
            capacity = all_resources.get(resource_name, 0)
            if capacity > 0:
                self.model.AddCumulative(usage_data['intervals'], usage_data['demands'], capacity)
                if s.debug:
                    print(f"  - Added cumulative constraint for '{resource_name}' with capacity {capacity} and {len(usage_data['intervals'])} tasks (real and dummy).")

        print(f"[INFO] Added cumulative constraints for {len(resource_to_tasks)} unique resources, including non-working hours.")

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

            # Determine the shift
            time_of_day = start_minutes % (24 * 60)
            assigned_shift = 'Unknown'

            team_shifts = []
            if task_info.get('is_quality'):
                team_shifts = self.scheduler.quality_team_shifts.get(task_info['team'], [])
            elif task_info.get('is_customer'):
                team_shifts = self.scheduler.customer_team_shifts.get(task_info['team'], [])
            else:
                team_key = task_info.get('team_skill', task_info.get('team'))
                base_team = team_key.split(' (')[0]
                team_shifts = self.scheduler.team_shifts.get(base_team, [])

            for shift_name in team_shifts:
                shift_info = self.scheduler.shift_hours.get(shift_name)
                if shift_info:
                    start_h, start_m = self.scheduler._parse_shift_time(shift_info['start'])
                    end_h, end_m = self.scheduler._parse_shift_time(shift_info['end'])
                    shift_start_min = start_h * 60 + start_m
                    shift_end_min = end_h * 60 + end_m

                    if shift_start_min < shift_end_min: # Normal shift
                        if shift_start_min <= time_of_day < shift_end_min:
                            assigned_shift = shift_name
                            break
                    else: # Split overnight shift part
                        if shift_start_min <= time_of_day:
                            assigned_shift = shift_name
                            break
                        # Note: The other part of the overnight shift is handled as a separate shift

            # Map split shift back to original name for display
            for original, parts in self.scheduler.overnight_shifts.items():
                if assigned_shift in parts:
                    assigned_shift = original
                    break

            schedule[task_id] = {
                'start_time': start_datetime + timedelta(minutes=start_minutes),
                'end_time': start_datetime + timedelta(minutes=end_minutes),
                'team': task_info.get('team'), 'team_skill': task_info.get('team_skill'),
                'skill': task_info.get('skill'), 'product': task_info.get('product'),
                'duration': task_info.get('duration'), 'mechanics_required': task_info.get('mechanics_required'),
                'is_quality': task_info.get('is_quality', False), 'is_customer': task_info.get('is_customer', False),
                'task_type': task_info.get('task_type'), 'original_task_id': task_info.get('original_task_id'),
                'shift': assigned_shift
            }
        print(f"[INFO] Extracted schedule for {len(schedule)} tasks.")
        return schedule
