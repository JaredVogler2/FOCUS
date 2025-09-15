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
        self.task_vars = defaultdict(list)
        self.horizon = 0
        self.working_intervals = []

    def _get_non_working_intervals(self):
        """
        Calculates non-working intervals (weekends and holidays for all products).
        Returns a list of (start_minute, end_minute) tuples.
        """
        non_working_intervals = []
        start_date = self.scheduler.start_date
        horizon_days = self.horizon // (24 * 60)

        # A day is a holiday if it's a holiday for ALL product lines.
        all_product_lines = self.scheduler.delivery_dates.keys()
        if not all_product_lines:
            common_holidays = set()
        else:
            # Initialize with the holidays of the first product line
            first_product = list(all_product_lines)[0]
            common_holidays = set(d.date() for d in self.scheduler.holidays.get(first_product, []))
            # Find the intersection with all other product lines' holidays
            for product in all_product_lines:
                product_holidays = set(d.date() for d in self.scheduler.holidays.get(product, []))
                common_holidays.intersection_update(product_holidays)

        for day_offset in range(horizon_days + 1):
            current_date = start_date + timedelta(days=day_offset)
            is_non_working = False

            # Check for weekends (Saturday=5, Sunday=6)
            if current_date.weekday() >= 5:
                is_non_working = True
            # Check for common holidays
            elif current_date.date() in common_holidays:
                is_non_working = True

            if is_non_working:
                # Non-working day is a 24-hour interval
                start_minute = day_offset * 24 * 60
                end_minute = start_minute + 24 * 60
                non_working_intervals.append((start_minute, end_minute))

        return non_working_intervals

    def _get_working_intervals(self):
        """
        Calculates working time intervals by taking the inverse of non-working intervals.
        """
        non_working_intervals = self._get_non_working_intervals()

        working_intervals = []
        last_end = 0
        for start, end in sorted(non_working_intervals):
            if start > last_end:
                working_intervals.append((last_end, start))
            last_end = end

        if last_end < self.horizon:
            working_intervals.append((last_end, self.horizon))

        return working_intervals

    def _add_interval_in_working_time_constraint(self, interval, start_var, end_var):
        """
        Adds a constraint to the model ensuring that the given interval variable
        is fully contained within one of the working_intervals.
        """
        bool_vars = []
        for i, (w_start, w_end) in enumerate(self.working_intervals):
            b = self.model.NewBoolVar(f'{interval.Name()}_in_w_interval_{i}')
            # An interval is contained if its start is >= working start AND its end is <= working end.
            self.model.Add(start_var >= w_start).OnlyEnforceIf(b)
            self.model.Add(end_var <= w_end).OnlyEnforceIf(b)
            bool_vars.append(b)

        # The interval must be in exactly one of the working intervals.
        self.model.Add(sum(bool_vars) == 1)

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
        Creates the core CP-SAT variables. Tasks >= 2h can be split into two parts.
        All tasks and task parts are constrained to run only during working time.
        """
        print("[INFO] Creating CP-SAT task variables with splitting logic...")
        self.working_intervals = self._get_working_intervals()

        for task_id, task_info in self.scheduler.tasks.items():
            duration = int(task_info['duration'])

            if duration >= 120:  # Task is splittable
                # Durations for part 1 and 2 must be at least 1 hour (60 min)
                duration1 = self.model.NewIntVar(60, duration - 60, f'{task_id}_part1_duration')
                # To ensure part 2 is also at least 60 min, we can define it in terms of duration1
                duration2 = self.model.NewIntVar(60, duration - 60, f'{task_id}_part2_duration')
                self.model.Add(duration1 + duration2 == duration)

                # Variables for Part 1
                start1 = self.model.NewIntVar(0, self.horizon, f'{task_id}_part1_start')
                end1 = self.model.NewIntVar(0, self.horizon, f'{task_id}_part1_end')
                interval1 = self.model.NewIntervalVar(start1, duration1, end1, f'{task_id}_part1_interval')

                # Variables for Part 2
                start2 = self.model.NewIntVar(0, self.horizon, f'{task_id}_part2_start')
                end2 = self.model.NewIntVar(0, self.horizon, f'{task_id}_part2_end')
                interval2 = self.model.NewIntervalVar(start2, duration2, end2, f'{task_id}_part2_interval')

                # Part 1 must end before Part 2 starts
                self.model.Add(end1 <= start2)

                self.task_vars[task_id].append({'start': start1, 'end': end1, 'interval': interval1, 'duration': duration1, 'part': 1})
                self.task_vars[task_id].append({'start': start2, 'end': end2, 'interval': interval2, 'duration': duration2, 'part': 2})

                # Each part must be fully contained within a working interval
                self._add_interval_in_working_time_constraint(interval1, start1, end1)
                self._add_interval_in_working_time_constraint(interval2, start2, end2)

            else:  # Non-splittable task
                start_var = self.model.NewIntVar(0, self.horizon, f'{task_id}_start')
                end_var = self.model.NewIntVar(0, self.horizon, f'{task_id}_end')
                interval_var = self.model.NewIntervalVar(start_var, duration, end_var, f'{task_id}_interval')

                self.task_vars[task_id].append({'start': start_var, 'end': end_var, 'interval': interval_var, 'duration': duration, 'part': 0})

                # The entire task must be contained in a single working interval
                self._add_interval_in_working_time_constraint(interval_var, start_var, end_var)

        print(f"[INFO] Created variables for {len(self.task_vars)} tasks.")

    def _add_precedence_constraints(self):
        """
        Adds precedence constraints, accounting for split tasks.
        """
        print("[INFO] Adding precedence constraints...")
        dependencies = self.scheduler.build_dynamic_dependencies()
        for const in dependencies:
            pred_id = const['First']
            succ_id = const['Second']

            if pred_id not in self.task_vars or succ_id not in self.task_vars:
                continue

            # Predecessor is the last part of the first task
            pred_vars = self.task_vars[pred_id][-1]
            # Successor is the first part of the second task
            succ_vars = self.task_vars[succ_id][0]

            relationship = const['Relationship']
            if relationship == 'Finish <= Start': self.model.Add(pred_vars['end'] <= succ_vars['start'])
            elif relationship == 'Finish = Start': self.model.Add(pred_vars['end'] == succ_vars['start'])
            elif relationship == 'Start <= Start': self.model.Add(pred_vars['start'] <= succ_vars['start'])
            elif relationship == 'Start = Start': self.model.Add(pred_vars['start'] == succ_vars['start'])
            elif relationship == 'Finish <= Finish': self.model.Add(pred_vars['end'] <= succ_vars['end'])
            else: self.model.Add(pred_vars['end'] <= succ_vars['start'])
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
                earliest_start_dt = earliest_start_dt.replace(hour=6, minute=0, second=0, microsecond=0)
                earliest_start_minutes = int((earliest_start_dt - self.scheduler.start_date).total_seconds() / 60)

                # The first part of the task cannot start before the part is available
                self.model.Add(self.task_vars[task_id][0]['start'] >= earliest_start_minutes)
                late_part_constraints_added += 1
        print(f"[INFO] Added {late_part_constraints_added} late part timing constraints.")

    def _add_resource_constraints(self):
        """
        Adds resource constraints, accounting for split tasks.
        """
        print("[INFO] Adding resource constraints...")
        resource_to_tasks = defaultdict(lambda: {'intervals': [], 'demands': []})

        for task_id, task_info in self.scheduler.tasks.items():
            task_parts = self.task_vars[task_id]

            for part_vars in task_parts:
                interval = part_vars['interval']

                # The demand is the same for all parts of a task
                demand = task_info['mechanics_required']

                if task_info.get('is_quality', False):
                    quality_team = task_info.get('team')
                    if quality_team:
                        resource_to_tasks[quality_team]['intervals'].append(interval)
                        resource_to_tasks[quality_team]['demands'].append(demand)

                    primary_task_id = task_info.get('primary_task')
                    if primary_task_id and primary_task_id in self.scheduler.tasks:
                        primary_task_info = self.scheduler.tasks[primary_task_id]
                        mechanic_team_resource = primary_task_info.get('team_skill')
                        if mechanic_team_resource:
                            resource_to_tasks[mechanic_team_resource]['intervals'].append(interval)
                            resource_to_tasks[mechanic_team_resource]['demands'].append(demand)

                elif task_info.get('is_customer', False):
                    customer_team = task_info.get('team')
                    if customer_team:
                        resource_to_tasks[customer_team]['intervals'].append(interval)
                        resource_to_tasks[customer_team]['demands'].append(demand)

                else: # Standard Production, Rework, or Late Part
                    mechanic_team_resource = task_info.get('team_skill')
                    if mechanic_team_resource:
                        resource_to_tasks[mechanic_team_resource]['intervals'].append(interval)
                        resource_to_tasks[mechanic_team_resource]['demands'].append(demand)

        all_resources = {**self.scheduler.team_capacity, **self.scheduler.quality_team_capacity, **self.scheduler.customer_team_capacity}
        for resource_name, capacity in all_resources.items():
            if resource_name in resource_to_tasks and capacity > 0:
                intervals = resource_to_tasks[resource_name]['intervals']
                demands = resource_to_tasks[resource_name]['demands']
                self.model.AddCumulative(intervals, demands, capacity)
                if self.scheduler.debug:
                    print(f"  - Added cumulative constraint for '{resource_name}' with capacity {capacity} and {len(intervals)} tasks/parts.")

        print(f"[INFO] Added cumulative constraints for {len(resource_to_tasks)} unique resources.")

    def _set_objective(self):
        """
        Defines the optimization objective to minimize total lateness.
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
            # The product makespan is the maximum end time of the LAST part of all terminal tasks.
            for task_id in terminal_tasks:
                last_part_end_var = self.task_vars[task_id][-1]['end']
                self.model.Add(last_part_end_var <= product_makespan)

            delivery_deadline_minutes = int((delivery_date - start_datetime).total_seconds() / 60)
            lateness_var = self.model.NewIntVar(0, self.horizon, f'{product}_lateness')
            self.model.Add(product_makespan - delivery_deadline_minutes <= lateness_var)
            all_lateness_vars.append(lateness_var)

        if all_lateness_vars:
            self.model.Minimize(sum(all_lateness_vars))
        print(f"[INFO] Objective set to minimize the sum of {len(all_lateness_vars)} product lateness variables.")

    def solve(self):
        """
        Builds and solves the CP-SAT model.
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
        Extracts the schedule from the solver, creating separate entries for split tasks.
        """
        print("[INFO] Extracting solution from solver...")
        schedule = {}
        start_datetime = self.scheduler.start_date

        for task_id, task_parts in self.task_vars.items():
            task_info = self.scheduler.tasks[task_id]
            is_split = len(task_parts) > 1

            for i, part_vars in enumerate(task_parts):
                # For split tasks, create a unique ID that can be traced back to the original.
                part_id = f"{task_id}---part{i+1}" if is_split else task_id

                start_minutes = solver.Value(part_vars['start'])
                end_minutes = solver.Value(part_vars['end'])
                duration_minutes = solver.Value(part_vars['duration'])

                schedule[part_id] = {
                    'start_time': start_datetime + timedelta(minutes=start_minutes),
                    'end_time': start_datetime + timedelta(minutes=end_minutes),
                    'team': task_info.get('team'),
                    'team_skill': task_info.get('team_skill'),
                    'skill': task_info.get('skill'),
                    'product': task_info.get('product'),
                    'duration': duration_minutes,
                    'mechanics_required': task_info.get('mechanics_required'),
                    'is_quality': task_info.get('is_quality', False),
                    'is_customer': task_info.get('is_customer', False),
                    'task_type': task_info.get('task_type'),
                    'original_task_id': task_info.get('original_task_id'),
                    'is_split_part': is_split
                }
        print(f"[INFO] Extracted schedule for {len(schedule)} tasks/parts.")
        return schedule
