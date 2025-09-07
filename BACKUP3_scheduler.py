"""
Production Scheduler with Enhanced Task Relationships and 1:1 Team Mapping
Part 1: Imports, Class Initialization, and Core Data Loading
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict, deque
import heapq
from typing import Dict, List, Set, Tuple, Optional
import warnings
import copy
import sys
import argparse
import re
import types
import csv

warnings.filterwarnings('ignore')


class ProductionScheduler:
    """
    Production scheduling system with enhanced features:
    - All task relationship types (FS, F=S, FF, SS, SF, S=S)
    - 1:1 mechanic to quality team mapping
    - Target lateness optimization
    - Comprehensive validation
    """

    def __init__(self, csv_file_path='scheduling_data.csv', debug=False, late_part_delay_days=1.0):
        """
        Initialize scheduler with CSV file containing all tables.
        """
        self.csv_path = csv_file_path
        self.debug = debug
        self.late_part_delay_days = late_part_delay_days

        # Task data structures
        self.tasks = {}
        self.baseline_task_data = {}
        self.task_instance_map = {}
        self.instance_to_product = {}
        self.instance_to_original_task = {}

        # Quality inspection tracking
        self.quality_inspections = {}
        self.quality_requirements = {}

        # Constraint structures
        self.precedence_constraints = []
        self.late_part_constraints = []
        self.rework_constraints = []

        # Product-specific task tracking
        self.product_remaining_ranges = {}
        self.late_part_tasks = {}
        self.rework_tasks = {}
        self.on_dock_dates = {}

        # Resource and scheduling data
        self.team_shifts = {}
        self.team_capacity = {}
        self.quality_team_shifts = {}
        self.quality_team_capacity = {}
        self.shift_hours = {}
        self.delivery_dates = {}
        self.holidays = defaultdict(set)

        # Scheduling results
        self.task_schedule = {}
        self.global_priority_list = []
        self._dynamic_constraints_cache = None
        self._critical_path_cache = {}

        # Store original capacities for reset
        self._original_team_capacity = {}
        self._original_quality_capacity = {}

        # Counter for unique task instance IDs
        self._next_instance_id = 1

    def debug_print(self, message, force=False):
        """Print debug message if debug mode is enabled or forced"""
        if self.debug or force:
            print(message)

    def parse_csv_sections(self, file_content):
        """Parse CSV file content into separate sections based on ==== markers"""
        sections = {}
        current_section = None
        current_data = []

        for line in file_content.strip().split('\n'):
            if '====' in line and line.strip().startswith('===='):
                if current_section and current_data:
                    sections[current_section] = '\n'.join(current_data)
                    if self.debug:
                        print(f"[DEBUG] Saved section '{current_section}' with {len(current_data)} lines")
                current_section = line.replace('=', '').strip()
                current_data = []
            else:
                if line.strip():
                    current_data.append(line)

        if current_section and current_data:
            sections[current_section] = '\n'.join(current_data)
            if self.debug:
                print(f"[DEBUG] Saved section '{current_section}' with {len(current_data)} lines")

        return sections

    def create_task_instance_id(self, product, task_id, task_type='baseline'):
        """Create a unique task instance ID"""
        if task_type == 'baseline':
            return f"{product}_{task_id}"
        else:
            return f"{task_type}_{task_id}"

    def map_mechanic_to_quality_team(self, mechanic_team):
        """
        Map mechanic team to corresponding quality team (1:1 mapping)
        Mechanic Team 1 -> Quality Team 1
        Mechanic Team 2 -> Quality Team 2, etc.
        """
        if not mechanic_team:
            return None

        # Extract team number from mechanic team name
        match = re.search(r'(\d+)', mechanic_team)
        if match:
            team_number = match.group(1)
            quality_team = f'Quality Team {team_number}'

            # Verify this quality team exists
            if quality_team in self.quality_team_capacity:
                return quality_team

        print(f"[WARNING] Could not map '{mechanic_team}' to a quality team")
        return None

    def load_data_from_csv(self):
        """Load all data from the CSV file with correct product-task instance handling"""
        print(f"\n[DEBUG] Starting to load data from {self.csv_path}")

        # Clear any cached data
        self._dynamic_constraints_cache = None
        self._critical_path_cache = {}

        # Read the CSV file
        try:
            with open(self.csv_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            print("[WARNING] UTF-8 decoding failed, trying latin-1...")
            with open(self.csv_path, 'r', encoding='latin-1') as f:
                content = f.read()

        # Remove BOM if present
        if content.startswith('\ufeff'):
            print("[WARNING] Removing BOM from file")
            content = content[1:]

        sections = self.parse_csv_sections(content)
        print(f"[DEBUG] Found {len(sections)} sections in CSV file")

        # CRITICAL: Load shift hours FIRST from CSV
        self._load_shift_hours(sections)

        # Then load team capacities (which use shifts)
        self._load_team_capacities_and_schedules(sections)

        # Then load task relationships and definitions
        self._load_task_definitions(sections)

        # Load product lines and create instances
        self._load_product_lines(sections)

        # Now load quality inspections (team mapping will work now)
        self._load_quality_inspections(sections)

        # Load late parts and rework
        self._load_late_parts_and_rework(sections)

        # Load remaining data (holidays, etc.)
        self._load_holidays(sections)

        # Validate and fix quality team assignments
        self._validate_and_fix_quality_assignments()

        self._print_summary()

    def _load_shift_hours(self, sections):
        """Load shift working hours from CSV"""
        if "SHIFT WORKING HOURS" in sections:
            import csv
            reader = csv.reader(sections["SHIFT WORKING HOURS"].splitlines())

            # Initialize shift_hours dict
            self.shift_hours = {}

            for row in reader:
                if row and row[0] != 'Shift' and len(row) >= 3:
                    shift_name = row[0].strip()
                    start_time = row[1].strip()
                    end_time = row[2].strip()

                    self.shift_hours[shift_name] = {
                        'start': start_time,
                        'end': end_time
                    }

            print(f"[DEBUG] Loaded shift hours for {len(self.shift_hours)} shifts:")
            for shift, hours in self.shift_hours.items():
                print(f"  - {shift}: {hours['start']} to {hours['end']}")
        else:
            # Fallback to defaults if not in CSV
            print("[WARNING] SHIFT WORKING HOURS not found in CSV, using defaults")
            self.shift_hours = {
                '1st': {'start': '6:00', 'end': '14:30'},
                '2nd': {'start': '14:30', 'end': '23:00'},
                '3rd': {'start': '23:00', 'end': '6:30'}
            }

        # Create alias for compatibility
        self.shift_definitions = self.shift_hours

    def _load_team_capacities_and_schedules(self, sections):
        """Load team capacities and working schedules with skill-specific shift inheritance"""

        # Load mechanic team capacities
        if "MECHANIC TEAM CAPACITY" in sections:
            reader = csv.reader(sections["MECHANIC TEAM CAPACITY"].splitlines())
            for row in reader:
                if row and row[0] != 'Mechanic Team':
                    team = row[0].strip()
                    capacity = int(row[1].strip())
                    self.team_capacity[team] = capacity
                    self._original_team_capacity[team] = capacity
            print(f"[DEBUG] Loaded capacity for {len(self.team_capacity)} mechanic teams")

        # Load quality team capacities
        if "QUALITY TEAM CAPACITY" in sections:
            reader = csv.reader(sections["QUALITY TEAM CAPACITY"].splitlines())
            for row in reader:
                if row and row[0] != 'Quality Team':
                    team = row[0].strip()
                    capacity = int(row[1].strip())
                    self.quality_team_capacity[team] = capacity
                    self._original_quality_capacity[team] = capacity
            print(f"[DEBUG] Loaded capacity for {len(self.quality_team_capacity)} quality teams")

        # Load mechanic team shifts - STORE AS LISTS
        if "MECHANIC TEAM WORKING CALENDARS" in sections:
            reader = csv.reader(sections["MECHANIC TEAM WORKING CALENDARS"].splitlines())
            for row in reader:
                if row and row[0] != 'Mechanic Team':
                    team = row[0].strip()
                    shifts = row[1].strip()
                    self.team_shifts[team] = [shifts]  # Store as list!
            print(f"[DEBUG] Loaded {len(self.team_shifts)} mechanic team schedules")

        # Load quality team shifts - STORE AS LISTS
        self.quality_team_shifts = {}
        if "QUALITY TEAM WORKING CALENDARS" in sections:
            reader = csv.reader(sections["QUALITY TEAM WORKING CALENDARS"].splitlines())
            for row in reader:
                if row and row[0] != 'Quality Team':
                    team = row[0].strip()
                    shifts = row[1].strip()
                    self.quality_team_shifts[team] = [shifts]  # Store as list!
            print(f"[DEBUG] Loaded {len(self.quality_team_shifts)} quality team schedules")

        # Ensure ALL quality teams have shifts
        for team in self.quality_team_capacity:
            if team not in self.quality_team_shifts or not self.quality_team_shifts[team]:
                match = re.search(r'(\d+)', team)
                if match:
                    team_number = match.group(1)
                    mechanic_base = f'Mechanic Team {team_number}'
                    if mechanic_base in self.team_shifts:
                        # Copy the list, not just reference
                        self.quality_team_shifts[team] = self.team_shifts[mechanic_base].copy()
                        if self.debug:
                            print(
                                f"[DEBUG] Quality {team} inheriting shift {self.team_shifts[mechanic_base]} from {mechanic_base}")
                    else:
                        # Default based on team number pattern
                        team_num = int(team_number)
                        if team_num in [1, 4, 7, 10]:
                            self.quality_team_shifts[team] = ["1st"]
                        elif team_num in [2, 5, 8]:
                            self.quality_team_shifts[team] = ["2nd"]
                        else:
                            self.quality_team_shifts[team] = ["3rd"]
                else:
                    self.quality_team_shifts[team] = ["1st"]

        # Map shifts from base teams to skill-specific teams
        shifts_inherited = 0
        for team_name in list(self.team_capacity.keys()):
            if " (Skill " in team_name and team_name not in self.team_shifts:
                base_team = team_name.split(" (Skill")[0]
                if base_team in self.team_shifts:
                    # Copy the list from base team
                    self.team_shifts[team_name] = self.team_shifts[base_team].copy()
                    shifts_inherited += 1
                else:
                    # Default to 1st shift as list
                    self.team_shifts[team_name] = ["1st"]

        if shifts_inherited > 0:
            print(f"[DEBUG] Inherited shifts for {shifts_inherited} skill-specific mechanic teams")

        # Final validation
        print(f"[DEBUG] Final shift assignments:")
        print(f"  - Mechanic teams with shifts: {len([t for t in self.team_shifts if self.team_shifts[t]])}")
        print(
            f"  - Quality teams with shifts: {len([t for t in self.quality_team_shifts if self.quality_team_shifts[t]])}")

    def _load_task_definitions(self, sections):
        """Load task relationships and definitions"""

        # Load Task Relationships
        if "TASK RELATIONSHIPS TABLE" in sections:
            from io import StringIO
            df = pd.read_csv(StringIO(sections["TASK RELATIONSHIPS TABLE"]))
            df.columns = df.columns.str.strip()
            for col in ['First', 'Second']:
                if col in df.columns:
                    df[col] = df[col].astype(int)

            if 'Relationship Type' not in df.columns and 'Relationship' not in df.columns:
                df['Relationship Type'] = 'Finish <= Start'
            elif 'Relationship' in df.columns and 'Relationship Type' not in df.columns:
                df['Relationship Type'] = df['Relationship']

            self.precedence_constraints = df.to_dict('records')
            print(f"[DEBUG] Loaded {len(self.precedence_constraints)} baseline task relationships")

        # Load Task Duration and Resources
        if "TASK DURATION AND RESOURCE TABLE" in sections:
            from io import StringIO
            df = pd.read_csv(StringIO(sections["TASK DURATION AND RESOURCE TABLE"]))
            df.columns = df.columns.str.strip()

            # Check if Skill Code column exists
            has_skill_column = 'Skill Code' in df.columns
            if has_skill_column:
                print(f"[DEBUG] Skill Code column detected in task definitions")

            task_count = 0
            for _, row in df.iterrows():
                try:
                    task_id = int(row['Task'])
                    if pd.isna(row.get('Duration (minutes)')) or pd.isna(row.get('Resource Type')) or pd.isna(
                            row.get('Mechanics Required')):
                        print(f"[WARNING] Skipping incomplete task row: {row}")
                        continue

                    team = row['Resource Type'].strip()

                    # Handle skill code if present
                    if has_skill_column and pd.notna(row.get('Skill Code')):
                        skill = row['Skill Code'].strip()
                        team_skill = f"{team} ({skill})"
                    else:
                        skill = None
                        team_skill = team

                    self.baseline_task_data[task_id] = {
                        'duration': int(row['Duration (minutes)']),
                        'team': team,  # Base team for dashboard filtering
                        'skill': skill,  # Skill subset (can be None)
                        'team_skill': team_skill,  # Combined identifier for scheduling
                        'mechanics_required': int(row['Mechanics Required']),
                        'is_quality': False,
                        'task_type': 'Production'
                    }
                    task_count += 1
                except (ValueError, KeyError) as e:
                    print(f"[WARNING] Error processing task row: {row}, Error: {e}")
                    continue

            print(f"[DEBUG] Loaded {task_count} baseline task definitions")
            if has_skill_column:
                # Count tasks per team-skill combination
                skill_counts = defaultdict(int)
                for task_data in self.baseline_task_data.values():
                    skill_counts[task_data['team_skill']] += 1
                print(f"[DEBUG] Task distribution across team-skill combinations:")
                for team_skill, count in sorted(skill_counts.items()):
                    print(f"  - {team_skill}: {count} tasks")

    def _load_product_lines(self, sections):
        """Load product lines and create task instances"""

        # Load Product Line Delivery Schedule
        if "PRODUCT LINE DELIVERY SCHEDULE" in sections:
            from io import StringIO
            df = pd.read_csv(StringIO(sections["PRODUCT LINE DELIVERY SCHEDULE"]))
            df.columns = df.columns.str.strip()
            for _, row in df.iterrows():
                product = row['Product Line'].strip()
                self.delivery_dates[product] = pd.to_datetime(row['Delivery Date'])
            print(f"[DEBUG] Loaded delivery dates for {len(self.delivery_dates)} product lines")

        # Load Product Line Jobs and CREATE TASK INSTANCES
        if "PRODUCT LINE JOBS" in sections:
            from io import StringIO
            df = pd.read_csv(StringIO(sections["PRODUCT LINE JOBS"]))
            df.columns = df.columns.str.strip()

            print(f"\n[DEBUG] Creating task instances for each product...")
            total_instances = 0

            for _, row in df.iterrows():
                product = row['Product Line'].strip()
                start_task = int(row['Task Start'])
                end_task = int(row['Task End'])

                self.product_remaining_ranges[product] = (start_task, end_task)

                product_instances = 0
                for task_id in range(start_task, end_task + 1):
                    if task_id in self.baseline_task_data:
                        instance_id = self.create_task_instance_id(product, task_id, 'baseline')
                        # Copy ALL fields from baseline_task_data including team, skill, and team_skill
                        task_data = self.baseline_task_data[task_id].copy()
                        task_data['product'] = product
                        task_data['original_task_id'] = task_id

                        # The task_data now includes:
                        # - 'team': base team for dashboard (e.g., "Mechanic Team 1")
                        # - 'skill': skill code if present (e.g., "Skill 1") or None
                        # - 'team_skill': combined for scheduling (e.g., "Mechanic Team 1 (Skill 1)")
                        # - 'duration', 'mechanics_required', 'is_quality', 'task_type'

                        self.tasks[instance_id] = task_data
                        self.task_instance_map[(product, task_id)] = instance_id
                        self.instance_to_product[instance_id] = product
                        self.instance_to_original_task[instance_id] = task_id

                        product_instances += 1
                        total_instances += 1

                completed = start_task - 1 if start_task > 1 else 0
                print(f"[DEBUG]   {product}: Created {product_instances} instances (tasks {start_task}-{end_task})")
                print(f"           Already completed: tasks 1-{completed}")

            print(f"[DEBUG] Total baseline task instances created: {total_instances}")

            # Debug: Show sample of team-skill distribution in instances
            if total_instances > 0:
                team_skill_instance_counts = defaultdict(int)
                for task_info in self.tasks.values():
                    if 'team_skill' in task_info:
                        team_skill_instance_counts[task_info['team_skill']] += 1

                if team_skill_instance_counts:
                    print(f"\n[DEBUG] Instance distribution by team-skill:")
                    for team_skill, count in sorted(team_skill_instance_counts.items())[:10]:  # Show first 10
                        print(f"  - {team_skill}: {count} instances")

    def _load_quality_inspections(self, sections):
        """Load quality inspections - team capacity should be loaded by now"""

        if "QUALITY INSPECTION REQUIREMENTS" in sections:
            from io import StringIO
            df = pd.read_csv(StringIO(sections["QUALITY INSPECTION REQUIREMENTS"]))
            df.columns = df.columns.str.strip()
            qi_count = 0
            qi_without_team = 0

            for _, row in df.iterrows():
                primary_task_id = int(row['Primary Task'])
                qi_task_id = int(row['Quality Task'])

                for product in self.delivery_dates.keys():
                    start_task, end_task = self.product_remaining_ranges.get(product, (1, 100))

                    if start_task <= primary_task_id <= end_task:
                        primary_instance_id = self.task_instance_map.get((product, primary_task_id))
                        if primary_instance_id:
                            # Get the primary task's team
                            primary_task_info = self.tasks.get(primary_instance_id, {})
                            primary_team = primary_task_info.get('team', '')

                            # Map mechanic team to quality team (1:1 mapping)
                            quality_team = self.map_mechanic_to_quality_team(primary_team)

                            if not quality_team:
                                qi_without_team += 1
                                if self.debug:
                                    print(
                                        f"[WARNING] No quality team for QI of task {primary_instance_id} (team: {primary_team})")

                            qi_instance_id = f"{product}_QI_{qi_task_id}"

                            self.tasks[qi_instance_id] = {
                                'duration': int(row['Quality Duration (minutes)']),
                                'team': quality_team,
                                'mechanics_required': int(row['Quality Headcount Required']),
                                'is_quality': True,
                                'task_type': 'Quality Inspection',
                                'primary_task': primary_instance_id,
                                'product': product,
                                'original_task_id': qi_task_id
                            }

                            self.quality_inspections[qi_instance_id] = {
                                'primary_task': primary_instance_id,
                                'headcount': int(row['Quality Headcount Required'])
                            }

                            self.quality_requirements[primary_instance_id] = qi_instance_id
                            self.instance_to_product[qi_instance_id] = product
                            self.instance_to_original_task[qi_instance_id] = qi_task_id
                            qi_count += 1

            print(f"[DEBUG] Created {qi_count} quality inspection instances")
            if qi_without_team > 0:
                print(f"[WARNING] {qi_without_team} QI tasks could not be assigned teams")

    def _load_holidays(self, sections):
        """Load holiday calendar"""

        if "PRODUCT LINE HOLIDAY CALENDAR" in sections:
            from io import StringIO
            df = pd.read_csv(StringIO(sections["PRODUCT LINE HOLIDAY CALENDAR"]))
            df.columns = df.columns.str.strip()
            holiday_count = 0

            for _, row in df.iterrows():
                try:
                    product = row['Product Line'].strip()
                    holiday_date = pd.to_datetime(row['Date'])
                    self.holidays[product].add(holiday_date)
                    holiday_count += 1
                except (ValueError, KeyError) as e:
                    print(f"[WARNING] Error processing holiday row: {row}, Error: {e}")
                    continue
            print(f"[DEBUG] Loaded {holiday_count} holiday entries")

    ################

    def _load_late_parts_and_rework(self, sections):
        """Load late parts and rework tasks with team/skill inherited from dependent baseline tasks"""

        # First load all constraints to understand the dependency structure

        # Load Late Parts Relationships
        if "LATE PARTS RELATIONSHIPS TABLE" in sections:
            from io import StringIO
            df = pd.read_csv(StringIO(sections["LATE PARTS RELATIONSHIPS TABLE"]))
            df.columns = df.columns.str.strip()
            lp_count = 0
            has_product_column = 'Product Line' in df.columns

            for _, row in df.iterrows():
                try:
                    first_task = str(row['First']).strip()
                    second_task = str(row['Second']).strip()
                    on_dock_date = pd.to_datetime(row['Estimated On Dock Date'])
                    product_line = row['Product Line'].strip() if has_product_column and pd.notna(
                        row.get('Product Line')) else None

                    relationship = row.get('Relationship Type', 'Finish <= Start').strip() if pd.notna(
                        row.get('Relationship Type')) else 'Finish <= Start'

                    self.late_part_constraints.append({
                        'First': first_task,
                        'Second': second_task,
                        'Relationship': relationship,
                        'On_Dock_Date': on_dock_date,
                        'Product_Line': product_line
                    })

                    self.on_dock_dates[first_task] = on_dock_date
                    lp_count += 1
                except (ValueError, KeyError) as e:
                    print(f"[WARNING] Error processing late part relationship row: {row}, Error: {e}")
                    continue
            print(f"[DEBUG] Loaded {lp_count} late part relationships")

        # Load Rework Relationships
        if "REWORK RELATIONSHIPS TABLE" in sections:
            from io import StringIO
            df = pd.read_csv(StringIO(sections["REWORK RELATIONSHIPS TABLE"]))
            df.columns = df.columns.str.strip()
            rw_count = 0
            has_product_column = 'Product Line' in df.columns

            for _, row in df.iterrows():
                try:
                    first_task = str(row['First']).strip()
                    second_task = str(row['Second']).strip()
                    product_line = row['Product Line'].strip() if has_product_column and pd.notna(
                        row.get('Product Line')) else None

                    relationship = 'Finish <= Start'
                    if 'Relationship Type' in row and pd.notna(row['Relationship Type']):
                        relationship = row['Relationship Type'].strip()
                    elif 'Relationship' in row and pd.notna(row['Relationship']):
                        relationship = row['Relationship'].strip()

                    self.rework_constraints.append({
                        'First': first_task,
                        'Second': second_task,
                        'Relationship': relationship,
                        'Product_Line': product_line
                    })

                    rw_count += 1
                except (ValueError, KeyError) as e:
                    print(f"[WARNING] Error processing rework relationship row: {row}, Error: {e}")
                    continue
            print(f"[DEBUG] Loaded {rw_count} rework relationships")

        # Helper function to find the ultimate baseline task by tracing dependencies
        def find_baseline_task_for_dependency(task_id, product_line=None):
            """Recursively trace dependencies to find the ultimate baseline production task"""
            visited = set()
            to_check = [(task_id, product_line)]

            while to_check:
                current_task, current_product = to_check.pop(0)

                if current_task in visited:
                    continue
                visited.add(current_task)

                # Check if current_task is a baseline task (numeric and in range)
                if current_task.isdigit():
                    task_num = int(current_task)
                    # Check if this is a baseline task for the product
                    if current_product:
                        if (current_product, task_num) in self.task_instance_map:
                            # Found a baseline task!
                            instance_id = self.task_instance_map[(current_product, task_num)]
                            if instance_id in self.tasks:
                                return self.tasks[instance_id], instance_id
                    else:
                        # Try to find in any product
                        for prod in self.delivery_dates.keys():
                            if (prod, task_num) in self.task_instance_map:
                                instance_id = self.task_instance_map[(prod, task_num)]
                                if instance_id in self.tasks:
                                    return self.tasks[instance_id], instance_id

                # Look for what this task is a predecessor to
                found_successor = False

                # Check late part constraints
                for constraint in self.late_part_constraints:
                    if constraint['First'] == current_task:
                        next_task = constraint['Second']
                        next_product = constraint.get('Product_Line', current_product)
                        to_check.append((next_task, next_product))
                        found_successor = True

                # Check rework constraints
                for constraint in self.rework_constraints:
                    if constraint['First'] == current_task:
                        next_task = constraint['Second']
                        next_product = constraint.get('Product_Line', current_product)
                        to_check.append((next_task, next_product))
                        found_successor = True

                # If no successor found and we haven't found a baseline task, return None
                if not found_successor and len(to_check) == 0:
                    return None, None

            return None, None

        # Load Late Parts Task Details
        if "LATE PARTS TASK DETAILS" in sections:
            from io import StringIO
            df = pd.read_csv(StringIO(sections["LATE PARTS TASK DETAILS"]))
            df.columns = df.columns.str.strip()
            lp_task_count = 0
            lp_inherited_count = 0

            for _, row in df.iterrows():
                try:
                    task_id = str(row['Task']).strip()

                    if pd.isna(row.get('Duration (minutes)')) or pd.isna(row.get('Resource Type')) or pd.isna(
                            row.get('Mechanics Required')):
                        print(f"[WARNING] Skipping incomplete late part task row: {row}")
                        continue

                    # Find product from constraints
                    product = None
                    for constraint in self.late_part_constraints:
                        if constraint['First'] == task_id and constraint.get('Product_Line'):
                            product = constraint['Product_Line']
                            break

                    # Find the baseline task this late part ultimately feeds into
                    baseline_task, baseline_instance_id = find_baseline_task_for_dependency(task_id, product)

                    if baseline_task:
                        # Inherit team and skill from baseline task
                        base_team = baseline_task.get('team')
                        skill = baseline_task.get('skill')
                        team_skill = baseline_task.get('team_skill')

                        if self.debug:
                            print(
                                f"[DEBUG] Late part {task_id} inheriting team/skill from {baseline_instance_id}: {team_skill}")
                        lp_inherited_count += 1
                    else:
                        # Fallback to CSV-defined team or default
                        base_team = row['Resource Type'].strip()
                        skill = 'Skill 1'  # Default skill
                        team_skill = f"{base_team} ({skill})"

                        # Verify this team+skill exists in capacity
                        if team_skill not in self.team_capacity:
                            # Find first available skill for this base team
                            for cap_team in self.team_capacity:
                                if cap_team.startswith(base_team + " ("):
                                    team_skill = cap_team
                                    # Extract skill from team_skill
                                    skill_match = re.search(r'\((.*?)\)', team_skill)
                                    if skill_match:
                                        skill = skill_match.group(1)
                                    break

                        if self.debug:
                            print(f"[WARNING] Late part {task_id} could not inherit team/skill, using {team_skill}")

                    instance_id = task_id

                    self.tasks[instance_id] = {
                        'duration': int(row['Duration (minutes)']),
                        'team': base_team,
                        'skill': skill,
                        'team_skill': team_skill,
                        'mechanics_required': int(row['Mechanics Required']),
                        'is_quality': False,
                        'task_type': 'Late Part',
                        'product': product,
                        'original_task_id': task_id
                    }

                    self.late_part_tasks[instance_id] = True
                    if product:
                        self.instance_to_product[instance_id] = product
                    self.instance_to_original_task[instance_id] = task_id

                    lp_task_count += 1
                except (ValueError, KeyError) as e:
                    print(f"[WARNING] Error processing late part task row: {row}, Error: {e}")
                    continue

            print(
                f"[DEBUG] Created {lp_task_count} late part task instances ({lp_inherited_count} inherited team/skill)")

        # Load Rework Task Details
        if "REWORK TASK DETAILS" in sections:
            from io import StringIO
            df = pd.read_csv(StringIO(sections["REWORK TASK DETAILS"]))
            df.columns = df.columns.str.strip()
            rw_task_count = 0
            rw_qi_count = 0
            rw_inherited_count = 0

            for _, row in df.iterrows():
                try:
                    task_id = str(row['Task']).strip()

                    if pd.isna(row.get('Duration (minutes)')) or pd.isna(row.get('Resource Type')) or pd.isna(
                            row.get('Mechanics Required')):
                        print(f"[WARNING] Skipping incomplete rework task row: {row}")
                        continue

                    # Find product from constraints
                    product = None
                    for constraint in self.rework_constraints:
                        if constraint['First'] == task_id and constraint.get('Product_Line'):
                            product = constraint['Product_Line']
                            break
                        elif constraint['Second'] == task_id and constraint.get('Product_Line'):
                            product = constraint['Product_Line']
                            break

                    # Find the baseline task this rework ultimately feeds into
                    baseline_task, baseline_instance_id = find_baseline_task_for_dependency(task_id, product)

                    if baseline_task:
                        # Inherit team and skill from baseline task
                        base_team = baseline_task.get('team')
                        skill = baseline_task.get('skill')
                        team_skill = baseline_task.get('team_skill')

                        if self.debug:
                            print(
                                f"[DEBUG] Rework {task_id} inheriting team/skill from {baseline_instance_id}: {team_skill}")
                        rw_inherited_count += 1
                    else:
                        # Fallback to CSV-defined team or default
                        base_team = row['Resource Type'].strip()
                        skill = 'Skill 1'  # Default skill
                        team_skill = f"{base_team} ({skill})"

                        # Verify this team+skill exists in capacity
                        if team_skill not in self.team_capacity:
                            # Find first available skill for this base team
                            for cap_team in self.team_capacity:
                                if cap_team.startswith(base_team + " ("):
                                    team_skill = cap_team
                                    # Extract skill from team_skill
                                    skill_match = re.search(r'\((.*?)\)', team_skill)
                                    if skill_match:
                                        skill = skill_match.group(1)
                                    break

                        if self.debug:
                            print(f"[WARNING] Rework {task_id} could not inherit team/skill, using {team_skill}")

                    instance_id = task_id

                    self.tasks[instance_id] = {
                        'duration': int(row['Duration (minutes)']),
                        'team': base_team,
                        'skill': skill,
                        'team_skill': team_skill,
                        'mechanics_required': int(row['Mechanics Required']),
                        'is_quality': False,
                        'task_type': 'Rework',
                        'product': product,
                        'original_task_id': task_id
                    }

                    self.rework_tasks[instance_id] = True
                    if product:
                        self.instance_to_product[instance_id] = product
                    self.instance_to_original_task[instance_id] = task_id

                    # Check if rework task needs quality inspection
                    needs_qi = row.get('Needs QI', 'Yes').strip() if pd.notna(row.get('Needs QI')) else 'Yes'
                    qi_duration = int(row['QI Duration (minutes)']) if pd.notna(
                        row.get('QI Duration (minutes)')) else 30
                    qi_headcount = int(row['QI Headcount']) if pd.notna(row.get('QI Headcount')) else 1

                    if needs_qi.lower() in ['yes', 'y', '1', 'true']:
                        qi_instance_id = f"QI_{task_id}"

                        # Get the quality team based on the rework task's base team
                        quality_team = self.map_mechanic_to_quality_team(base_team)

                        self.quality_requirements[instance_id] = qi_instance_id

                        self.tasks[qi_instance_id] = {
                            'duration': qi_duration,
                            'team': quality_team,
                            'skill': None,  # Quality teams don't have skills
                            'team_skill': quality_team,
                            'mechanics_required': qi_headcount,
                            'is_quality': True,
                            'task_type': 'Quality Inspection',
                            'primary_task': instance_id,
                            'product': product,
                            'original_task_id': qi_instance_id
                        }

                        self.quality_inspections[qi_instance_id] = {
                            'primary_task': instance_id,
                            'headcount': qi_headcount
                        }

                        if product:
                            self.instance_to_product[qi_instance_id] = product
                        self.instance_to_original_task[qi_instance_id] = qi_instance_id

                        rw_qi_count += 1

                    rw_task_count += 1
                except (ValueError, KeyError) as e:
                    print(f"[WARNING] Error processing rework task row: {row}, Error: {e}")
                    continue

            print(f"[DEBUG] Created {rw_task_count} rework task instances ({rw_inherited_count} inherited team/skill)")
            if rw_qi_count > 0:
                print(f"[DEBUG] Created {rw_qi_count} quality inspections for rework tasks")

    def _validate_and_fix_quality_assignments(self):
        """Validate and fix all quality inspection team assignments"""
        qi_without_teams = 0
        qi_fixed = 0
        qi_with_teams = {}

        for task_id, task_info in self.tasks.items():
            if task_info.get('is_quality', False):
                team = task_info.get('team')
                if not team:
                    qi_without_teams += 1
                    # Try to fix it
                    if task_id in self.quality_inspections:
                        primary_task_id = self.quality_inspections[task_id].get('primary_task')
                        if primary_task_id and primary_task_id in self.tasks:
                            primary_team = self.tasks[primary_task_id].get('team')
                            quality_team = self.map_mechanic_to_quality_team(primary_team)
                            if quality_team:
                                task_info['team'] = quality_team
                                qi_fixed += 1
                                if self.debug:
                                    print(f"[FIX] Assigned {quality_team} to orphaned QI {task_id}")
                else:
                    if team not in qi_with_teams:
                        qi_with_teams[team] = 0
                    qi_with_teams[team] += 1

        if qi_fixed > 0:
            print(f"[DEBUG] Fixed {qi_fixed} quality inspection team assignments")

        if qi_without_teams - qi_fixed > 0:
            print(f"[WARNING] {qi_without_teams - qi_fixed} QI tasks still without teams!")

    def _print_summary(self):
        """Print comprehensive summary of loaded data"""
        print(f"\n" + "=" * 80)
        print("DATA LOADING SUMMARY")
        print("=" * 80)

        task_type_counts = defaultdict(int)
        product_task_counts = defaultdict(int)

        for instance_id, task_info in self.tasks.items():
            task_type_counts[task_info['task_type']] += 1
            if 'product' in task_info and task_info['product']:
                product_task_counts[task_info['product']] += 1

        print(f"\n[DEBUG] Task Instance Summary:")
        print(f"Total task instances: {len(self.tasks)}")
        print("\nBreakdown by type:")
        for task_type, count in sorted(task_type_counts.items()):
            print(f"  - {task_type}: {count}")

        print(f"\n[DEBUG] Task instances per product:")
        for product in sorted(self.delivery_dates.keys()):
            count = product_task_counts.get(product, 0)
            start, end = self.product_remaining_ranges.get(product, (0, 0))
            print(f"  - {product}: {count} instances (baseline tasks {start}-{end})")

        if self.late_part_tasks:
            print(f"\n[DEBUG] Late Part Tasks:")
            print(f"  - Total late part tasks: {len(self.late_part_tasks)}")
            print(f"  - Late part constraints: {len(self.late_part_constraints)}")

            lp_by_product = defaultdict(int)
            for task_id in self.late_part_tasks:
                product = self.instance_to_product.get(task_id, 'Unassigned')
                lp_by_product[product] += 1

            for product, count in sorted(lp_by_product.items()):
                print(f"    {product}: {count} late part tasks")

        if self.rework_tasks:
            print(f"\n[DEBUG] Rework Tasks:")
            print(f"  - Total rework tasks: {len(self.rework_tasks)}")
            print(f"  - Rework constraints: {len(self.rework_constraints)}")

            rw_by_product = defaultdict(int)
            for task_id in self.rework_tasks:
                product = self.instance_to_product.get(task_id, 'Unassigned')
                rw_by_product[product] += 1

            for product, count in sorted(rw_by_product.items()):
                print(f"    {product}: {count} rework tasks")

        if self.quality_inspections:
            print(f"\n[DEBUG] Quality Inspections:")
            print(f"  - Total QI instances: {len(self.quality_inspections)}")
            print(f"  - Tasks requiring QI: {len(self.quality_requirements)}")

        print(f"\n[DEBUG] Resources:")
        print(f"  - Mechanic teams: {len(self.team_capacity)}")
        total_mechanics = sum(self.team_capacity.values())
        print(f"    Total mechanic capacity: {total_mechanics}")
        for team, capacity in sorted(self.team_capacity.items()):
            shifts = self.team_shifts.get(team, [])
            print(f"    {team}: {capacity} people, shifts: {shifts}")

        print(f"  - Quality teams: {len(self.quality_team_capacity)}")
        total_quality = sum(self.quality_team_capacity.values())
        print(f"    Total quality capacity: {total_quality}")
        for team, capacity in sorted(self.quality_team_capacity.items()):
            shifts = self.quality_team_shifts.get(team, [])
            print(f"    {team}: {capacity} people, shifts: {shifts}")

        print(f"\n[DEBUG] Delivery Schedule:")
        for product, date in sorted(self.delivery_dates.items()):
            print(f"  - {product}: {date.strftime('%Y-%m-%d')}")

        if self.holidays:
            print(f"\n[DEBUG] Holidays:")
            total_holidays = sum(len(dates) for dates in self.holidays.values())
            print(f"  - Total holiday entries: {total_holidays}")
            for product, dates in sorted(self.holidays.items()):
                if dates:
                    print(f"    {product}: {len(dates)} holidays")

        print(f"\n[DEBUG] Constraints Summary:")
        print(f"  - Baseline precedence constraints: {len(self.precedence_constraints)}")
        print(f"  - Late part constraints: {len(self.late_part_constraints)}")
        print(f"  - Rework constraints: {len(self.rework_constraints)}")
        total_constraints = (len(self.precedence_constraints) +
                             len(self.late_part_constraints) +
                             len(self.rework_constraints))
        print(f"  - Total constraints defined: {total_constraints}")
        print("=" * 80)

    def build_dynamic_dependencies(self):
        """
        Build dependency graph with support for ALL relationship types and string task IDs
        """
        if self._dynamic_constraints_cache is not None:
            return self._dynamic_constraints_cache

        self.debug_print(f"\n[DEBUG] Building dynamic dependencies with all relationship types...")
        dynamic_constraints = []

        # 1. Add baseline task constraints (product-specific)
        for constraint in self.precedence_constraints:
            first_task_id = constraint['First']
            second_task_id = constraint['Second']

            relationship = constraint.get('Relationship Type') or constraint.get('Relationship', 'Finish <= Start')
            relationship = self._normalize_relationship_type(relationship)

            for product in self.delivery_dates.keys():
                first_instance = self.task_instance_map.get((product, first_task_id))
                second_instance = self.task_instance_map.get((product, second_task_id))

                if first_instance and second_instance:
                    if first_instance in self.quality_requirements and relationship in ['Finish <= Start',
                                                                                        'Finish = Start']:
                        qi_instance = self.quality_requirements[first_instance]

                        dynamic_constraints.append({
                            'First': first_instance,
                            'Second': qi_instance,
                            'Relationship': 'Finish = Start',
                            'Product': product
                        })

                        dynamic_constraints.append({
                            'First': qi_instance,
                            'Second': second_instance,
                            'Relationship': relationship,
                            'Product': product
                        })
                    else:
                        dynamic_constraints.append({
                            'First': first_instance,
                            'Second': second_instance,
                            'Relationship': relationship,
                            'Product': product
                        })

        # 2. Add late part constraints
        for lp_constraint in self.late_part_constraints:
            first_task = lp_constraint['First']
            second_task = lp_constraint['Second']
            product = lp_constraint.get('Product_Line')
            relationship = self._normalize_relationship_type(lp_constraint.get('Relationship', 'Finish <= Start'))

            # First task - check if it's already a prefixed string or needs conversion
            if first_task in self.tasks:
                # It's already a valid task instance ID (e.g., "LP_1001")
                first_instance = first_task
            elif str(first_task).startswith('LP_'):
                # It has the prefix but might not be in tasks
                first_instance = first_task if first_task in self.tasks else None
            else:
                # Legacy numeric ID - add prefix
                first_instance = f"LP_{first_task}" if f"LP_{first_task}" in self.tasks else None

            # Second task - could be prefixed, numeric baseline, or another special task
            second_instance = None
            if second_task in self.tasks:
                # It's already a valid task instance ID
                second_instance = second_task
            elif str(second_task).startswith('LP_') or str(second_task).startswith('RW_'):
                # It has a prefix
                second_instance = second_task if second_task in self.tasks else None
            elif str(second_task).isdigit():
                # It's a numeric ID - could be baseline or need prefix
                task_num = int(second_task)
                if task_num < 1000:
                    # It's a baseline task
                    second_instance = self.task_instance_map.get((product, task_num)) if product else None
                    # If no product specified, try to find it in any product
                    if not second_instance:
                        for prod in self.delivery_dates.keys():
                            second_instance = self.task_instance_map.get((prod, task_num))
                            if second_instance:
                                break
                else:
                    # Check if it needs LP_ or RW_ prefix
                    if f"LP_{second_task}" in self.tasks:
                        second_instance = f"LP_{second_task}"
                    elif f"RW_{second_task}" in self.tasks:
                        second_instance = f"RW_{second_task}"

            if first_instance and second_instance and first_instance in self.tasks and second_instance in self.tasks:
                dynamic_constraints.append({
                    'First': first_instance,
                    'Second': second_instance,
                    'Relationship': relationship,
                    'Type': 'Late Part',
                    'Product': product
                })

        # 3. Add rework constraints
        for rw_constraint in self.rework_constraints:
            first_task = rw_constraint['First']
            second_task = rw_constraint['Second']
            relationship = self._normalize_relationship_type(rw_constraint.get('Relationship', 'Finish <= Start'))
            product = rw_constraint.get('Product_Line')

            # First task - check if it's already prefixed or needs conversion
            if first_task in self.tasks:
                # It's already a valid task instance ID (e.g., "RW_401")
                first_instance = first_task
            elif str(first_task).startswith('RW_'):
                # It has the prefix but might not be in tasks
                first_instance = first_task if first_task in self.tasks else None
            else:
                # Legacy numeric ID - add prefix
                first_instance = f"RW_{first_task}" if f"RW_{first_task}" in self.tasks else None

            # Second task - could be prefixed, numeric baseline, or another special task
            second_instance = None
            if second_task in self.tasks:
                # It's already a valid task instance ID
                second_instance = second_task
            elif str(second_task).startswith('RW_') or str(second_task).startswith('LP_'):
                # It has a prefix
                second_instance = second_task if second_task in self.tasks else None
            elif str(second_task).isdigit():
                # It's a numeric ID
                task_num = int(second_task)
                if task_num < 1000:
                    # It's a baseline task
                    second_instance = self.task_instance_map.get((product, task_num)) if product else None
                    # If no product specified, try to find it in any product
                    if not second_instance:
                        for prod in self.delivery_dates.keys():
                            second_instance = self.task_instance_map.get((prod, task_num))
                            if second_instance:
                                break
                else:
                    # Check if it needs RW_ or LP_ prefix
                    if f"RW_{second_task}" in self.tasks:
                        second_instance = f"RW_{second_task}"
                    elif f"LP_{second_task}" in self.tasks:
                        second_instance = f"LP_{second_task}"

            if first_instance and second_instance and first_instance in self.tasks and second_instance in self.tasks:
                if first_instance in self.quality_requirements and relationship in ['Finish <= Start',
                                                                                    'Finish = Start']:
                    qi_instance = self.quality_requirements[first_instance]

                    dynamic_constraints.append({
                        'First': first_instance,
                        'Second': qi_instance,
                        'Relationship': 'Finish = Start',
                        'Type': 'Rework QI',
                        'Product': product
                    })

                    dynamic_constraints.append({
                        'First': qi_instance,
                        'Second': second_instance,
                        'Relationship': relationship,
                        'Type': 'Rework',
                        'Product': product
                    })
                else:
                    dynamic_constraints.append({
                        'First': first_instance,
                        'Second': second_instance,
                        'Relationship': relationship,
                        'Type': 'Rework',
                        'Product': product
                    })

        # 4. Add remaining QI constraints (for any QI relationships not already added)
        for primary_instance, qi_instance in self.quality_requirements.items():
            if not any(c['First'] == primary_instance and c['Second'] == qi_instance
                       for c in dynamic_constraints):
                dynamic_constraints.append({
                    'First': primary_instance,
                    'Second': qi_instance,
                    'Relationship': 'Finish = Start',
                    'Product': self.instance_to_product.get(primary_instance)
                })

        self.debug_print(f"[DEBUG] Total dynamic constraints: {len(dynamic_constraints)}")

        # Count relationships by type for debugging
        rel_counts = defaultdict(int)
        for c in dynamic_constraints:
            rel_counts[c['Relationship']] += 1

        if self.debug:
            for rel_type, count in sorted(rel_counts.items()):
                self.debug_print(f"  {rel_type}: {count}")

        self._dynamic_constraints_cache = dynamic_constraints
        return dynamic_constraints

    def get_successors(self, task_id):
        """Get all immediate successor tasks for a given task"""
        successors = []

        # Get dynamic constraints if not cached
        dynamic_constraints = self.build_dynamic_dependencies()

        # Find all tasks where this task is the 'First' (predecessor)
        for constraint in dynamic_constraints:
            if constraint['First'] == task_id:
                successors.append(constraint['Second'])

        return successors

    def get_predecessors(self, task_id):
        """Get all immediate predecessor tasks for a given task"""
        predecessors = []

        # Get dynamic constraints if not cached
        dynamic_constraints = self.build_dynamic_dependencies()

        # Find all tasks where this task is the 'Second' (successor)
        for constraint in dynamic_constraints:
            if constraint['Second'] == task_id:
                predecessors.append(constraint['First'])

        return predecessors

    def _normalize_relationship_type(self, relationship):
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

    def check_constraint_satisfied(self, first_schedule, second_schedule, relationship):
        """Check if a scheduling constraint is satisfied between two tasks"""
        if not first_schedule or not second_schedule:
            return True, None, None

        first_start = first_schedule['start_time']
        first_end = first_schedule['end_time']
        second_start = second_schedule['start_time']
        second_end = second_schedule['end_time']
        second_duration = second_schedule['duration']

        relationship = self._normalize_relationship_type(relationship)

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

    #####################

    def schedule_tasks(self, allow_late_delivery=False, silent_mode=False):
        """Schedule all task instances with proper error handling"""
        original_debug = self.debug
        if silent_mode:
            self.debug = False

        self.task_schedule = {}
        self._critical_path_cache = {}

        if not silent_mode and not self.validate_dag():
            raise ValueError("DAG validation failed!")

        dynamic_constraints = self.build_dynamic_dependencies()
        start_date = datetime(2025, 8, 22, 6, 0)

        constraints_by_second = defaultdict(list)
        constraints_by_first = defaultdict(list)

        for constraint in dynamic_constraints:
            constraints_by_second[constraint['Second']].append(constraint)
            constraints_by_first[constraint['First']].append(constraint)

        all_tasks = set(self.tasks.keys())
        total_tasks = len(all_tasks)
        ready_tasks = []

        if not silent_mode:
            print(f"\nStarting scheduling for {total_tasks} task instances...")

        # Find initially ready tasks
        tasks_with_incoming_constraints = set()
        tasks_with_outgoing_constraints = set()

        for constraint in dynamic_constraints:
            tasks_with_incoming_constraints.add(constraint['Second'])
            tasks_with_outgoing_constraints.add(constraint['First'])

        orphaned_tasks = all_tasks - tasks_with_incoming_constraints - tasks_with_outgoing_constraints

        if not silent_mode and orphaned_tasks:
            print(f"[DEBUG] Found {len(orphaned_tasks)} orphaned tasks with no constraints")

        for task in orphaned_tasks:
            priority = self.calculate_task_priority(task)
            heapq.heappush(ready_tasks, (priority, task))

        tasks_with_only_outgoing = tasks_with_outgoing_constraints - tasks_with_incoming_constraints
        for task in tasks_with_only_outgoing:
            priority = self.calculate_task_priority(task)
            heapq.heappush(ready_tasks, (priority, task))

        for task in tasks_with_incoming_constraints:
            constraints = constraints_by_second.get(task, [])
            has_blocking_constraints = False
            for c in constraints:
                rel = c['Relationship']
                if rel in ['Finish <= Start', 'Finish = Start', 'Finish <= Finish']:
                    has_blocking_constraints = True
                    break
            if not has_blocking_constraints:
                priority = self.calculate_task_priority(task)
                heapq.heappush(ready_tasks, (priority, task))

        if not silent_mode:
            print(f"[DEBUG] Initial ready queue has {len(ready_tasks)} tasks")

        scheduled_count = 0
        max_iterations = total_tasks * 10
        iteration_count = 0
        failed_tasks = set()
        task_retry_counts = defaultdict(int)

        # Track scheduling failures
        cannot_schedule = []
        far_future_schedules = []

        while ready_tasks and scheduled_count < total_tasks and iteration_count < max_iterations:
            iteration_count += 1

            if not ready_tasks:
                for task in all_tasks:
                    if task in self.task_schedule or task in failed_tasks:
                        continue

                    all_predecessors_scheduled = True
                    for constraint in constraints_by_second.get(task, []):
                        if constraint['First'] not in self.task_schedule:
                            all_predecessors_scheduled = False
                            break

                    if all_predecessors_scheduled:
                        priority = self.calculate_task_priority(task)
                        heapq.heappush(ready_tasks, (priority, task))

                if not ready_tasks:
                    if not silent_mode:
                        unscheduled = [t for t in all_tasks if t not in self.task_schedule and t not in failed_tasks]
                        print(f"[WARNING] No ready tasks but {len(unscheduled)} tasks remain unscheduled")
                    break

            priority, task_instance_id = heapq.heappop(ready_tasks)

            if task_retry_counts[task_instance_id] >= 3:
                if task_instance_id not in failed_tasks:
                    failed_tasks.add(task_instance_id)
                    if not silent_mode:
                        print(f"[ERROR] Task {task_instance_id} failed after 3 retries")
                continue

            task_info = self.tasks[task_instance_id]
            duration = task_info['duration']
            mechanics_needed = task_info['mechanics_required']
            is_quality = task_info['is_quality']
            task_type = task_info['task_type']
            product = task_info.get('product', 'Unknown')

            earliest_start = start_date
            latest_start_constraint = None

            if task_instance_id in self.late_part_tasks:
                earliest_start = self.get_earliest_start_for_late_part(task_instance_id)

            for constraint in constraints_by_second.get(task_instance_id, []):
                first_task = constraint['First']
                relationship = constraint['Relationship']

                if first_task in self.task_schedule:
                    first_schedule = self.task_schedule[first_task]

                    if relationship == 'Finish <= Start':
                        constraint_time = first_schedule['end_time']
                    elif relationship == 'Finish = Start':
                        constraint_time = first_schedule['end_time']
                    elif relationship == 'Start <= Start' or relationship == 'Start = Start':
                        constraint_time = first_schedule['start_time']
                    elif relationship == 'Finish <= Finish':
                        constraint_time = first_schedule['end_time'] - timedelta(minutes=duration)
                    elif relationship == 'Start <= Finish':
                        constraint_time = first_schedule['start_time'] - timedelta(minutes=duration)
                    else:
                        constraint_time = first_schedule['end_time']

                    earliest_start = max(earliest_start, constraint_time)

                    if relationship == 'Start = Start':
                        latest_start_constraint = first_schedule['start_time']

            if latest_start_constraint:
                earliest_start = latest_start_constraint

            try:
                if is_quality:
                    base_mechanic_team = task_info.get('team', '')
                    quality_team = self.map_mechanic_to_quality_team(base_mechanic_team)

                    if not quality_team:
                        print(f"[ERROR] Quality task {task_instance_id} has no team assigned!")
                        if task_instance_id in self.quality_inspections:
                            primary_task_id = self.quality_inspections[task_instance_id].get('primary_task')
                            if primary_task_id and primary_task_id in self.tasks:
                                primary_team = self.tasks[primary_task_id].get('team')
                                quality_team = self.map_mechanic_to_quality_team(primary_team)
                                if quality_team:
                                    task_info['team'] = primary_team
                                    print(f"[RECOVERY] Assigned {quality_team} to {task_instance_id}")

                        if not quality_team:
                            task_retry_counts[task_instance_id] += 1
                            if task_retry_counts[task_instance_id] < 3:
                                heapq.heappush(ready_tasks, (priority + 0.1, task_instance_id))
                            continue

                    result = self.get_next_working_time_with_capacity(
                        earliest_start, product, quality_team,
                        mechanics_needed, duration, is_quality=True)

                    # Check for None return (cannot schedule)
                    if result is None or result[0] is None:
                        cannot_schedule.append(task_instance_id)
                        task_retry_counts[task_instance_id] += 1
                        if task_retry_counts[task_instance_id] < 3:
                            heapq.heappush(ready_tasks, (priority + 0.1, task_instance_id))
                        else:
                            failed_tasks.add(task_instance_id)
                            if not silent_mode:
                                print(f"[FAILED] Cannot find slot for quality task {task_instance_id}")
                        continue

                    scheduled_start, shift = result
                    team_for_schedule = quality_team
                    base_team_for_schedule = quality_team

                else:
                    team_for_scheduling = task_info.get('team_skill', task_info['team'])

                    if '(' in team_for_scheduling and ')' in team_for_scheduling:
                        base_team = team_for_scheduling.split(' (')[0].strip()
                    else:
                        base_team = task_info.get('team', team_for_scheduling)

                    result = self.get_next_working_time_with_capacity(
                        earliest_start, product, team_for_scheduling,
                        mechanics_needed, duration, is_quality=False)

                    # Check for None return (cannot schedule)
                    if result is None or result[0] is None:
                        cannot_schedule.append(task_instance_id)
                        task_retry_counts[task_instance_id] += 1
                        if task_retry_counts[task_instance_id] < 3:
                            heapq.heappush(ready_tasks, (priority + 0.1, task_instance_id))
                        else:
                            failed_tasks.add(task_instance_id)
                            if not silent_mode:
                                print(f"[FAILED] Cannot find slot for mechanic task {task_instance_id}")
                        continue

                    scheduled_start, shift = result
                    team_for_schedule = team_for_scheduling
                    base_team_for_schedule = base_team

                # Check if scheduled to far future (year 7501 problem)
                if scheduled_start.year > 2030:
                    far_future_schedules.append(task_instance_id)
                    failed_tasks.add(task_instance_id)
                    if not silent_mode:
                        print(
                            f"[ERROR] Task {task_instance_id} scheduled to year {scheduled_start.year} - marking as failed")
                    continue

                scheduled_end = scheduled_start + timedelta(minutes=int(duration))

                self.task_schedule[task_instance_id] = {
                    'start_time': scheduled_start,
                    'end_time': scheduled_end,
                    'team': base_team_for_schedule,
                    'team_skill': team_for_schedule,
                    'skill': task_info.get('skill'),
                    'product': product,
                    'duration': duration,
                    'mechanics_required': mechanics_needed,
                    'is_quality': is_quality,
                    'task_type': task_type,
                    'shift': shift,
                    'original_task_id': self.instance_to_original_task.get(task_instance_id)
                }

                scheduled_count += 1

                # Add newly ready tasks
                for constraint in constraints_by_first.get(task_instance_id, []):
                    dependent = constraint['Second']
                    if dependent in self.task_schedule or dependent in failed_tasks:
                        continue

                    all_satisfied = True
                    for dep_constraint in constraints_by_second.get(dependent, []):
                        predecessor = dep_constraint['First']
                        if predecessor not in self.task_schedule:
                            all_satisfied = False
                            break

                    if all_satisfied and dependent not in [t[1] for t in ready_tasks]:
                        dep_priority = self.calculate_task_priority(dependent)
                        heapq.heappush(ready_tasks, (dep_priority, dependent))

            except Exception as e:
                if self.debug:
                    print(f"[ERROR] Failed to schedule {task_instance_id}: {str(e)}")
                task_retry_counts[task_instance_id] += 1
                if task_retry_counts[task_instance_id] < 3:
                    heapq.heappush(ready_tasks, (priority + 0.1, task_instance_id))
                else:
                    failed_tasks.add(task_instance_id)

        if not silent_mode:
            print(f"\n[DEBUG] Scheduling complete! Actually scheduled {scheduled_count}/{total_tasks} task instances.")
            if scheduled_count < total_tasks:
                unscheduled = total_tasks - scheduled_count
                print(f"[WARNING] {unscheduled} tasks could not be scheduled")

                if cannot_schedule:
                    print(f"[WARNING] {len(cannot_schedule)} tasks couldn't find time slots")

                if far_future_schedules:
                    print(f"[WARNING] {len(far_future_schedules)} tasks scheduled to far future (>2030)")

                unscheduled_list = [t for t in all_tasks if t not in self.task_schedule][:10]
                print(f"[DEBUG] First 10 unscheduled tasks: {unscheduled_list}")

        self.debug = original_debug

    def schedule_tasks_with_level_loading(self, allow_late_delivery=False, aggressiveness=0.5, silent_mode=False):
        """
        Schedule tasks with level-loading to smooth workforce utilization
        aggressiveness: 0.0 = pure ASAP, 1.0 = maximum level loading
        """
        original_debug = self.debug
        if silent_mode:
            self.debug = False

        self.task_schedule = {}
        self._critical_path_cache = {}

        if not silent_mode and not self.validate_dag():
            raise ValueError("DAG validation failed!")

        dynamic_constraints = self.build_dynamic_dependencies()
        start_date = datetime(2025, 8, 22, 6, 0)

        # Build constraint lookups
        constraints_by_second = defaultdict(list)
        constraints_by_first = defaultdict(list)

        for constraint in dynamic_constraints:
            constraints_by_second[constraint['Second']].append(constraint)
            constraints_by_first[constraint['First']].append(constraint)

        all_tasks = set(self.tasks.keys())
        total_tasks = len(all_tasks)
        ready_tasks = []

        if not silent_mode:
            print(f"\nStarting level-loaded scheduling for {total_tasks} task instances...")
            print(f"Level-loading aggressiveness: {aggressiveness:.1%}")

        # Find initially ready tasks (same as original)
        tasks_with_incoming_constraints = set()
        tasks_with_outgoing_constraints = set()

        for constraint in dynamic_constraints:
            tasks_with_incoming_constraints.add(constraint['Second'])
            tasks_with_outgoing_constraints.add(constraint['First'])

        # Tasks with no constraints at all should be ready immediately
        orphaned_tasks = all_tasks - tasks_with_incoming_constraints - tasks_with_outgoing_constraints

        if not silent_mode and orphaned_tasks:
            print(f"[DEBUG] Found {len(orphaned_tasks)} orphaned tasks with no constraints")

        # Add orphaned tasks to ready queue
        for task in orphaned_tasks:
            priority = self.calculate_task_priority(task)
            heapq.heappush(ready_tasks, (priority, task))

        # Add tasks that have only outgoing constraints (no incoming)
        tasks_with_only_outgoing = tasks_with_outgoing_constraints - tasks_with_incoming_constraints
        for task in tasks_with_only_outgoing:
            priority = self.calculate_task_priority(task)
            heapq.heappush(ready_tasks, (priority, task))

        # Add tasks with non-blocking incoming constraints
        for task in tasks_with_incoming_constraints:
            constraints = constraints_by_second.get(task, [])
            has_blocking_constraints = False
            for c in constraints:
                rel = c['Relationship']
                if rel in ['Finish <= Start', 'Finish = Start', 'Finish <= Finish']:
                    has_blocking_constraints = True
                    break
            if not has_blocking_constraints:
                priority = self.calculate_task_priority(task)
                heapq.heappush(ready_tasks, (priority, task))

        if not silent_mode:
            print(f"[DEBUG] Initial ready queue has {len(ready_tasks)} tasks")

        scheduled_count = 0
        max_iterations = total_tasks * 10
        iteration_count = 0
        failed_tasks = set()
        task_retry_counts = defaultdict(int)

        while ready_tasks and scheduled_count < total_tasks and iteration_count < max_iterations:
            iteration_count += 1

            if not ready_tasks:
                # Check if there are unscheduled tasks that should be ready now
                for task in all_tasks:
                    if task in self.task_schedule or task in failed_tasks:
                        continue

                    # Check if all predecessors are scheduled
                    all_predecessors_scheduled = True
                    for constraint in constraints_by_second.get(task, []):
                        if constraint['First'] not in self.task_schedule:
                            all_predecessors_scheduled = False
                            break

                    if all_predecessors_scheduled:
                        priority = self.calculate_task_priority(task)
                        heapq.heappush(ready_tasks, (priority, task))

                if not ready_tasks:
                    if not silent_mode:
                        unscheduled = [t for t in all_tasks if t not in self.task_schedule and t not in failed_tasks]
                        print(f"[WARNING] No ready tasks but {len(unscheduled)} tasks remain unscheduled")
                    break

            priority, task_instance_id = heapq.heappop(ready_tasks)

            if task_retry_counts[task_instance_id] >= 3:
                if task_instance_id not in failed_tasks:
                    failed_tasks.add(task_instance_id)
                    if not silent_mode:
                        print(f"[ERROR] Task {task_instance_id} failed after 3 retries")
                continue

            task_info = self.tasks[task_instance_id]
            duration = task_info['duration']
            mechanics_needed = task_info['mechanics_required']
            is_quality = task_info['is_quality']
            task_type = task_info['task_type']
            product = task_info.get('product', 'Unknown')

            # Extract proper team names
            if is_quality:
                base_mechanic_team = task_info.get('team', '')
                quality_team = self.map_mechanic_to_quality_team(base_mechanic_team)
                team = quality_team  # For level loading calculations
                team_for_scheduling = quality_team
                base_team = quality_team
            else:
                team_for_scheduling = task_info.get('team_skill', task_info['team'])
                # Extract base team
                if '(' in team_for_scheduling and ')' in team_for_scheduling:
                    base_team = team_for_scheduling.split(' (')[0].strip()
                else:
                    base_team = task_info.get('team', team_for_scheduling)
                team = team_for_scheduling  # For level loading calculations

            # Calculate earliest start based on constraints
            earliest_start = start_date
            latest_start_constraint = None

            # Handle late parts
            if task_instance_id in self.late_part_tasks:
                earliest_start = self.get_earliest_start_for_late_part(task_instance_id)

            # Check predecessor constraints
            for constraint in constraints_by_second.get(task_instance_id, []):
                first_task = constraint['First']
                relationship = constraint['Relationship']

                if first_task in self.task_schedule:
                    first_schedule = self.task_schedule[first_task]

                    if relationship == 'Finish <= Start':
                        constraint_time = first_schedule['end_time']
                    elif relationship == 'Finish = Start':
                        constraint_time = first_schedule['end_time']
                    elif relationship == 'Start <= Start' or relationship == 'Start = Start':
                        constraint_time = first_schedule['start_time']
                    elif relationship == 'Finish <= Finish':
                        constraint_time = first_schedule['end_time'] - timedelta(minutes=duration)
                    elif relationship == 'Start <= Finish':
                        constraint_time = first_schedule['start_time'] - timedelta(minutes=duration)
                    else:
                        constraint_time = first_schedule['end_time']

                    earliest_start = max(earliest_start, constraint_time)

                    if relationship == 'Start = Start':
                        latest_start_constraint = first_schedule['start_time']

            if latest_start_constraint:
                earliest_start = latest_start_constraint

            # NEW: Level-loading logic
            best_start_time = None
            best_shift = None
            best_score = float('inf')

            # Determine how far to look ahead based on aggressiveness
            if aggressiveness > 0:
                lookahead_days = int(5 * aggressiveness)  # 0-5 days based on aggressiveness
                lookahead_hours = int(48 * aggressiveness)  # Fine-grained search within days
            else:
                lookahead_days = 0
                lookahead_hours = 0

            # Try different start times to find best utilization
            test_time = earliest_start
            end_lookahead = earliest_start + timedelta(days=lookahead_days, hours=lookahead_hours)

            tested_times = []

            while test_time <= end_lookahead:
                # Check if this is a valid start time
                if self.is_working_day(test_time, product):
                    # Try to schedule at this time
                    try:
                        # Check if we can schedule here without violating constraints
                        test_end = test_time + timedelta(minutes=duration)

                        # Validate against all constraints
                        valid = True

                        # Check successor constraints (tasks already scheduled that depend on this)
                        for constraint in constraints_by_first.get(task_instance_id, []):
                            successor_id = constraint['Second']
                            if successor_id in self.task_schedule:
                                successor_schedule = self.task_schedule[successor_id]
                                relationship = constraint['Relationship']

                                test_schedule = {
                                    'start_time': test_time,
                                    'end_time': test_end,
                                    'duration': duration
                                }

                                is_satisfied, _, _ = self.check_constraint_satisfied(
                                    test_schedule, successor_schedule, relationship
                                )

                                if not is_satisfied:
                                    valid = False
                                    break

                        if valid:
                            # Check team availability at this time
                            potential_start, potential_shift = self.get_next_working_time_with_capacity(
                                test_time, product, team_for_scheduling, mechanics_needed, duration, is_quality
                            )

                            # Calculate utilization score for this time
                            day_util = self.calculate_day_utilization(team_for_scheduling, potential_start.date())
                            week_util = self.calculate_week_utilization(team_for_scheduling, potential_start.date())

                            # Score combines multiple factors
                            delay_days = (potential_start - earliest_start).total_seconds() / 86400

                            # Penalties and bonuses
                            delay_penalty = delay_days * (1 - aggressiveness) * 100
                            utilization_penalty = (
                                                              day_util ** 2) * aggressiveness  # Quadratic penalty for high utilization
                            week_balance_bonus = -((
                                                               week_util - 50) ** 2) * aggressiveness * 0.01  # Bonus for ~50% weekly utilization

                            # Check if this would create gaps in the schedule
                            gap_penalty = self.calculate_gap_penalty(team_for_scheduling, potential_start,
                                                                     test_end) * aggressiveness

                            total_score = delay_penalty + utilization_penalty + week_balance_bonus + gap_penalty

                            tested_times.append({
                                'time': potential_start,
                                'score': total_score,
                                'delay': delay_days,
                                'day_util': day_util,
                                'week_util': week_util
                            })

                            if total_score < best_score:
                                best_score = total_score
                                best_start_time = potential_start
                                best_shift = potential_shift

                    except Exception as e:
                        # Can't schedule at this time, continue
                        pass

                # Move to next test time
                test_time += timedelta(hours=1)

            # If no valid level-loaded time found, fall back to earliest
            if best_start_time is None:
                best_start_time, best_shift = self.get_next_working_time_with_capacity(
                    earliest_start, product, team_for_scheduling, mechanics_needed, duration, is_quality
                )

            # Schedule the task at the best time found
            try:
                scheduled_end = best_start_time + timedelta(minutes=int(duration))

                self.task_schedule[task_instance_id] = {
                    'start_time': best_start_time,
                    'end_time': scheduled_end,
                    'team': base_team,  # Base team for dashboard filtering
                    'team_skill': team_for_scheduling,  # Full team+skill for capacity
                    'skill': task_info.get('skill'),  # Skill code
                    'product': product,
                    'duration': duration,
                    'mechanics_required': mechanics_needed,
                    'is_quality': is_quality,
                    'task_type': task_type,
                    'shift': best_shift,
                    'original_task_id': self.instance_to_original_task.get(task_instance_id)
                }

                scheduled_count += 1

                if not silent_mode and scheduled_count % 100 == 0:
                    print(f"  Scheduled {scheduled_count}/{total_tasks} tasks...")

                # Add dependent tasks to ready queue
                for constraint in constraints_by_first.get(task_instance_id, []):
                    dependent = constraint['Second']
                    if dependent in self.task_schedule or dependent in failed_tasks:
                        continue

                    # Check if all predecessors are scheduled
                    all_satisfied = True
                    for dep_constraint in constraints_by_second.get(dependent, []):
                        predecessor = dep_constraint['First']
                        if predecessor not in self.task_schedule:
                            all_satisfied = False
                            break

                    if all_satisfied and dependent not in [t[1] for t in ready_tasks]:
                        dep_priority = self.calculate_task_priority(dependent)
                        heapq.heappush(ready_tasks, (dep_priority, dependent))

            except Exception as e:
                if self.debug:
                    print(f"[ERROR] Failed to schedule {task_instance_id}: {str(e)}")
                task_retry_counts[task_instance_id] += 1
                if task_retry_counts[task_instance_id] < 3:
                    heapq.heappush(ready_tasks, (priority + 0.1, task_instance_id))
                else:
                    failed_tasks.add(task_instance_id)

        if not silent_mode:
            print(f"\n[DEBUG] Level-loaded scheduling complete!")
            print(f"  Scheduled: {scheduled_count}/{total_tasks} tasks")
            if scheduled_count < total_tasks:
                unscheduled = total_tasks - scheduled_count
                print(f"[WARNING] {unscheduled} tasks could not be scheduled")

        self.debug = original_debug

    def calculate_week_utilization(self, team, reference_date):
        """Calculate average utilization for the week containing reference_date"""
        # Find start of week (Monday)
        week_start = reference_date - timedelta(days=reference_date.weekday())
        week_end = week_start + timedelta(days=6)

        total_util = 0
        working_days = 0

        for i in range(7):
            check_date = week_start + timedelta(days=i)
            if check_date.weekday() < 5:  # Monday-Friday
                util = self.calculate_day_utilization(team, check_date)
                total_util += util
                working_days += 1

        return total_util / working_days if working_days > 0 else 0

    def calculate_initial_utilization(self, days_to_check=1):
        """Calculate average utilization for first few days only (continuous flow assumption)"""
        if not self.task_schedule:
            return 0

        # Find earliest start date
        start_date = min(s['start_time'].date() for s in self.task_schedule.values())
        end_date = start_date + timedelta(days=days_to_check)

        total_util = 0
        team_count = 0

        for team in list(self.team_capacity.keys()) + list(self.quality_team_capacity.keys()):
            capacity = self.team_capacity.get(team, 0) or self.quality_team_capacity.get(team, 0)
            if capacity == 0:
                continue

            team_total_minutes = 0
            team_available_minutes = 0

            current_date = start_date
            while current_date < end_date:
                if self.is_working_day(datetime.combine(current_date, datetime.min.time()),
                                       list(self.delivery_dates.keys())[0]):
                    day_util = self.calculate_day_utilization(team, current_date)
                    team_total_minutes += day_util
                    team_available_minutes += 100  # Each day can be up to 100% utilized
                current_date += timedelta(days=1)

            if team_available_minutes > 0:
                team_avg_util = team_total_minutes / days_to_check
                total_util += team_avg_util
                team_count += 1

        return total_util / team_count if team_count > 0 else 0


    def calculate_gap_penalty(self, team, proposed_start, proposed_end):
        """Calculate penalty for creating gaps in the schedule"""
        penalty = 0

        # Find tasks for this team on the same day
        day_tasks = []
        for task_id, schedule in self.task_schedule.items():
            if schedule['team'] == team and schedule['start_time'].date() == proposed_start.date():
                day_tasks.append((schedule['start_time'], schedule['end_time']))

        if day_tasks:
            day_tasks.sort()

            # Check for gaps before and after proposed task
            for start, end in day_tasks:
                if end < proposed_start:
                    gap = (proposed_start - end).total_seconds() / 3600  # Gap in hours
                    if gap > 1:  # Penalty for gaps > 1 hour
                        penalty += gap * 10
                elif start > proposed_end:
                    gap = (start - proposed_end).total_seconds() / 3600
                    if gap > 1:
                        penalty += gap * 10

        return penalty

    def calculate_day_utilization(self, team, target_date):
        """Calculate the utilization for a team on a specific date"""
        total_minutes = 0
        capacity = self.team_capacity.get(team, 0) or self.quality_team_capacity.get(team, 0)

        if capacity == 0:
            return 0

        for task_id, schedule in self.task_schedule.items():
            if schedule['team'] == team:
                task_date = schedule['start_time'].date()
                if task_date == target_date:
                    total_minutes += schedule['duration'] * schedule['mechanics_required']

        # FIX: Use actual shift hours instead of hardcoded 8
        team_shifts = self.team_shifts.get(team, self.quality_team_shifts.get(team, ['1st']))
        total_available_minutes = 0

        for shift in team_shifts:
            shift_info = self.shift_hours.get(shift, {'start': '6:00', 'end': '14:30'})
            start_hour, start_min = self._parse_shift_time(shift_info['start'])
            end_hour, end_min = self._parse_shift_time(shift_info['end'])

            # Calculate shift duration
            if shift == '3rd':  # Crosses midnight
                shift_minutes = ((24 - start_hour) * 60 - start_min) + (end_hour * 60 + end_min)
            else:
                shift_minutes = (end_hour * 60 + end_min) - (start_hour * 60 + start_min)

            total_available_minutes += shift_minutes * capacity

        if total_available_minutes > 0:
            return (total_minutes / total_available_minutes) * 100
        return 0

    def calculate_peak_utilization(self):
        """Calculate peak single-day utilization across all teams"""
        if not self.task_schedule:
            return 0

        # Find date range
        all_dates = set()
        for schedule in self.task_schedule.values():
            all_dates.add(schedule['start_time'].date())

        if not all_dates:
            return 0

        peak_util = 0
        peak_date = None
        peak_team = None

        for check_date in sorted(all_dates)[:5]:  # Check first 5 days only
            for team in list(self.team_capacity.keys()) + list(self.quality_team_capacity.keys()):
                util = self.calculate_day_utilization(team, check_date)
                if util > peak_util:
                    peak_util = util
                    peak_date = check_date
                    peak_team = team

        if self.debug:
            print(f"Peak utilization: {peak_util:.1f}% on {peak_date} for {peak_team}")

        return peak_util

    def calculate_task_priority(self, task_instance_id):
        """Calculate priority for a task instance considering dependent task timing"""
        task_info = self.tasks[task_instance_id]


        # For late parts, check on-dock date
        if task_instance_id in self.late_part_tasks:
            # Check if we have an on-dock date
            if task_instance_id in self.on_dock_dates:
                on_dock_date = self.on_dock_dates[task_instance_id]
                days_until_available = (on_dock_date - datetime.now()).days
                # Priority based on how soon the part arrives
                return -3000 + (days_until_available * 10)  # Less urgent if arriving later
            return -3000

        # For quality inspections, inherit priority from primary task
        if task_instance_id in self.quality_inspections:
            primary_task = self.quality_inspections[task_instance_id].get('primary_task')
            if primary_task and primary_task in self.task_schedule:
                # QI should happen right after primary task
                return self.calculate_task_priority(primary_task) - 1
            return -2000

        # For rework tasks, consider when the dependent tasks need them
        if task_instance_id in self.rework_tasks:
            # Find all tasks that depend on this rework
            dynamic_constraints = self.build_dynamic_dependencies()
            dependent_tasks = []

            for constraint in dynamic_constraints:
                if constraint['First'] == task_instance_id:
                    dependent_tasks.append(constraint['Second'])

            if dependent_tasks:
                # Calculate the earliest dependent task's priority
                min_dependent_priority = float('inf')
                for dep_task in dependent_tasks:
                    if dep_task in self.tasks:
                        # Get the product and delivery date of the dependent task
                        dep_task_info = self.tasks[dep_task]
                        dep_product = dep_task_info.get('product')

                        if dep_product and dep_product in self.delivery_dates:
                            delivery_date = self.delivery_dates[dep_product]
                            days_to_delivery = (delivery_date - datetime.now()).days

                            # Calculate priority based on delivery urgency
                            dep_priority = (100 - days_to_delivery) * 20
                            min_dependent_priority = min(min_dependent_priority, dep_priority)

                if min_dependent_priority < float('inf'):
                    # Rework should be slightly higher priority than its dependents
                    # but not universally high priority
                    return min_dependent_priority - 100

            # Fallback for rework with no clear dependents
            return -500  # Still important but not top priority

        # Standard priority calculation for baseline tasks
        product = task_info.get('product')
        if product and product in self.delivery_dates:
            delivery_date = self.delivery_dates[product]
            days_to_delivery = (delivery_date - datetime.now()).days
        else:
            days_to_delivery = 999

        critical_path_length = self.calculate_critical_path_length(task_instance_id)
        duration = int(task_info['duration'])

        priority = (
                (100 - days_to_delivery) * 20 +
                (10000 - critical_path_length) * 5 +
                (100 - duration / 10) * 2
        )

        return priority

    def get_earliest_start_for_late_part(self, task_instance_id):
        """Calculate earliest start time for a late part task"""
        # task_instance_id is now like "LP_1001"
        if task_instance_id not in self.on_dock_dates:
            return datetime(2025, 8, 22, 6, 0)

        on_dock_date = self.on_dock_dates[task_instance_id]
        earliest_start = on_dock_date + timedelta(days=self.late_part_delay_days)
        earliest_start = earliest_start.replace(hour=6, minute=0, second=0, microsecond=0)
        return earliest_start

    def validate_dag(self):
        """Validate that the dependency graph is a DAG"""
        print("\nValidating task dependency graph...")

        dynamic_constraints = self.build_dynamic_dependencies()

        graph = defaultdict(set)
        all_tasks_in_constraints = set()

        for constraint in dynamic_constraints:
            first = constraint['First']
            second = constraint['Second']
            if constraint['Relationship'] in ['Finish <= Start', 'Finish = Start']:
                graph[first].add(second)
            all_tasks_in_constraints.add(first)
            all_tasks_in_constraints.add(second)

        missing_tasks = all_tasks_in_constraints - set(self.tasks.keys())
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

    def calculate_utilization_variance(self):
        """Calculate variance in daily utilization across all teams"""
        daily_utilizations = defaultdict(list)

        # Calculate utilization for each team for each day
        for team in list(self.team_capacity.keys()) + list(self.quality_team_capacity.keys()):
            team_tasks = [(t, s) for t, s in self.task_schedule.items() if s['team'] == team]

            if not team_tasks:
                continue

            # Find date range
            min_date = min(s['start_time'].date() for _, s in team_tasks)
            max_date = max(s['end_time'].date() for _, s in team_tasks)

            current_date = min_date
            while current_date <= max_date:
                if self.is_working_day(datetime.combine(current_date, datetime.min.time()),
                                       list(self.delivery_dates.keys())[0]):
                    util = self.calculate_day_utilization(team, current_date)
                    daily_utilizations[team].append(util)
                current_date += timedelta(days=1)

        # Calculate variance
        all_utilizations = []
        for team_utils in daily_utilizations.values():
            all_utilizations.extend(team_utils)

        if not all_utilizations:
            return 0

        mean = sum(all_utilizations) / len(all_utilizations)
        variance = sum((x - mean) ** 2 for x in all_utilizations) / len(all_utilizations)
        return variance

    def debug_scheduling_blockage(self):
        """Find why scheduling stops at task 140"""

        # Get the dynamic constraints
        constraints = self.build_dynamic_dependencies()

        # Find unscheduled tasks
        unscheduled = [t for t in self.tasks if t not in self.task_schedule]

        print(f"\n[BLOCKAGE ANALYSIS]")
        print(f"Scheduled: {len(self.task_schedule)} tasks")
        print(f"Unscheduled: {len(unscheduled)} tasks")

        # For first 10 unscheduled tasks, check why they can't schedule
        for task_id in unscheduled[:10]:
            task_info = self.tasks[task_id]
            print(f"\nTask {task_id}:")
            print(f"  Type: {task_info['task_type']}")
            print(f"  Team needed: {task_info.get('team_skill', task_info.get('team'))}")
            print(f"  Product: {task_info.get('product')}")

            # Check dependencies
            waiting_for = []
            for c in constraints:
                if c['Second'] == task_id:
                    if c['First'] not in self.task_schedule:
                        waiting_for.append(c['First'])

            if waiting_for:
                print(f"  BLOCKED BY: {waiting_for[:5]}")
            else:
                print(f"  NOT BLOCKED (should be ready)")

            # Check team availability
            team = task_info.get('team_skill', task_info.get('team'))
            if team:
                capacity = self.team_capacity.get(team, 0) or self.quality_team_capacity.get(team, 0)
                print(f"  Team capacity: {capacity}")

    def is_valid_start_time(self, task_id, proposed_start, constraints):
        """Check if starting a task at proposed_start violates any constraints"""
        task_info = self.tasks[task_id]
        duration = task_info['duration']
        proposed_end = proposed_start + timedelta(minutes=duration)

        # Check predecessors
        for constraint in constraints:
            if constraint['Second'] == task_id and constraint['First'] in self.task_schedule:
                predecessor = self.task_schedule[constraint['First']]
                relationship = constraint['Relationship']

                if not self.check_constraint_satisfied(predecessor,
                                                       {'start_time': proposed_start, 'end_time': proposed_end,
                                                        'duration': duration},
                                                       relationship)[0]:
                    return False

        # Check successors already scheduled
        for constraint in constraints:
            if constraint['First'] == task_id and constraint['Second'] in self.task_schedule:
                successor = self.task_schedule[constraint['Second']]
                relationship = constraint['Relationship']

                if not self.check_constraint_satisfied(
                        {'start_time': proposed_start, 'end_time': proposed_end, 'duration': duration},
                        successor, relationship)[0]:
                    return False

        return True

    def is_working_day(self, date, product_line):
        """Check if a date is a working day for a specific product line"""
        if date.weekday() >= 5:  # Weekend
            return False

        # FIX: Handle None or missing product_line
        if not product_line:
            return True  # If no product specified, assume working day

        if product_line not in self.holidays:
            return True  # If product not in holidays dict, assume working day

        # Now safe to check holidays
        if date.date() in [h.date() for h in self.holidays[product_line]]:
            return False

        return True

    def check_team_capacity_at_time(self, team, start_time, end_time, mechanics_needed):
        """Check if team has available capacity during specified time period"""
        capacity = self.team_capacity.get(team, 0) or self.quality_team_capacity.get(team, 0)
        team_tasks = [(task_id, sched) for task_id, sched in self.task_schedule.items()
                      if sched['team'] == team]

        current = start_time
        while current < end_time:
            usage = 0
            for task_id, sched in team_tasks:
                if sched['start_time'] <= current < sched['end_time']:
                    usage += sched['mechanics_required']
            if usage + mechanics_needed > capacity:
                return False
            current += timedelta(minutes=1)
        return True

    def get_next_working_time_with_capacity(self, current_time, product_line, team,
                                            mechanics_needed, duration, is_quality=False):
        """Find next available working time with sufficient team capacity - FIXED"""

        # Get capacity and shifts
        if is_quality:
            capacity = self.quality_team_capacity.get(team, 0)
            shifts = self.quality_team_shifts.get(team, ['1st'])
        else:
            capacity = self.team_capacity.get(team, 0)
            base_team = team.split('(')[0].strip() if '(' in team else team
            shifts = self.team_shifts.get(base_team, self.team_shifts.get(team, ['1st']))

        if capacity == 0 or mechanics_needed > capacity:
            return None, None

        # Start from current time
        search_time = current_time
        max_days_ahead = 30

        for days_ahead in range(max_days_ahead):
            check_date = (current_time + timedelta(days=days_ahead)).replace(
                hour=0, minute=0, second=0, microsecond=0)

            if not self.is_working_day(check_date, product_line):
                continue

            for shift in shifts:
                shift_info = self.shift_hours.get(shift)
                if not shift_info:
                    continue

                start_hour, start_min = self._parse_shift_time(shift_info['start'])
                end_hour, end_min = self._parse_shift_time(shift_info['end'])

                # Calculate shift boundaries
                if shift == '3rd':
                    # 3rd shift: 23:00 today to 6:00 tomorrow
                    shift_start = check_date.replace(hour=23, minute=0)
                    shift_end = (check_date + timedelta(days=1)).replace(hour=6, minute=0)

                    # Special case: if we're checking today and current time is 0:00-6:00,
                    # check if we're still in yesterday's 3rd shift
                    if days_ahead == 0 and current_time.hour < 6:
                        # We're in the tail end of yesterday's 3rd shift
                        shift_start = (check_date - timedelta(days=1)).replace(hour=23, minute=0)
                        shift_end = check_date.replace(hour=6, minute=0)
                else:
                    shift_start = check_date.replace(hour=start_hour, minute=start_min)
                    shift_end = check_date.replace(hour=end_hour, minute=end_min)

                # Skip if shift already ended
                if shift_end <= current_time:
                    continue

                # Find earliest possible start within this shift
                earliest_in_shift = max(shift_start, current_time)

                # Round up to next 15-minute mark
                minutes = earliest_in_shift.minute
                if minutes % 15 != 0:
                    rounded_minutes = ((minutes // 15) + 1) * 15
                    if rounded_minutes >= 60:
                        earliest_in_shift = earliest_in_shift.replace(minute=0) + timedelta(hours=1)
                    else:
                        earliest_in_shift = earliest_in_shift.replace(minute=rounded_minutes)

                # Check if task fits in shift
                task_end = earliest_in_shift + timedelta(minutes=duration)
                if task_end > shift_end:
                    continue

                # Check capacity
                conflicts = 0
                for task_id, schedule in self.task_schedule.items():
                    if schedule.get('team_skill', schedule.get('team')) == team:
                        # Check for time overlap
                        if (schedule['start_time'] < task_end and
                                schedule['end_time'] > earliest_in_shift):
                            conflicts += schedule.get('mechanics_required', 1)

                if capacity - conflicts >= mechanics_needed:
                    return earliest_in_shift, shift

        return None, None

    def _parse_shift_time(self, time_str):
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

    # Add this temporary debug method to scheduler.py
    def debug_unscheduled_tasks(self):
        """Debug why tasks aren't being scheduled"""
        unscheduled = [t for t in self.tasks if t not in self.task_schedule]

        print(f"\n[DEBUG] {len(unscheduled)} unscheduled tasks")

        # Group by team
        by_team = defaultdict(list)
        for task_id in unscheduled[:20]:  # First 20
            task_info = self.tasks[task_id]
            team = task_info.get('team_skill', task_info.get('team', 'NO_TEAM'))
            by_team[team].append(task_id)

        print("Sample unscheduled tasks by team:")
        for team, tasks in by_team.items():
            capacity = self.team_capacity.get(team, 0) or self.quality_team_capacity.get(team, 0)
            print(f"  {team}: {len(tasks)} tasks, capacity={capacity}")

            # Check if this team exists in capacity tables
            if team not in self.team_capacity and team not in self.quality_team_capacity:
                print(f"    WARNING: Team '{team}' not in capacity tables!")

    def calculate_critical_path_length(self, task_instance_id):
        """Calculate critical path length from this task"""
        if task_instance_id in self._critical_path_cache:
            return self._critical_path_cache[task_instance_id]

        dynamic_constraints = self.build_dynamic_dependencies()

        def get_path_length(task):
            if task in self._critical_path_cache:
                return self._critical_path_cache[task]

            max_successor_path = 0
            task_duration = self.tasks[task]['duration']

            for constraint in dynamic_constraints:
                if constraint['First'] == task:
                    successor = constraint['Second']
                    if successor in self.tasks:
                        successor_path = get_path_length(successor)
                        max_successor_path = max(max_successor_path, successor_path)

            self._critical_path_cache[task] = task_duration + max_successor_path
            return self._critical_path_cache[task]

        return get_path_length(task_instance_id)

    ###################

    def generate_global_priority_list(self, allow_late_delivery=True, silent_mode=False):
        """Generate priority list for all task instances"""
        self.schedule_tasks(allow_late_delivery=allow_late_delivery, silent_mode=silent_mode)

        conflicts = self.check_resource_conflicts()
        if conflicts and not silent_mode:
            print(f"\n[WARNING] Found {len(conflicts)} resource conflicts")

        priority_data = []

        for task_instance_id, schedule in self.task_schedule.items():
            slack = self.calculate_slack_time(task_instance_id)
            task_type = schedule['task_type']
            original_task_id = schedule.get('original_task_id')
            product = schedule.get('product', 'Unknown')

            # Get criticality from schedule if stored, or calculate it
            criticality = schedule.get('criticality')
            if not criticality:
                criticality = self.classify_task_criticality(task_instance_id)

            if task_type == 'Quality Inspection':
                primary_task = self.quality_inspections.get(task_instance_id, {}).get('primary_task')
                if primary_task:
                    primary_original = self.instance_to_original_task.get(primary_task, primary_task)
                    display_name = f"{product} QI for Task {primary_original}"
                else:
                    display_name = f"{product} QI {original_task_id}"
            elif task_type == 'Late Part':
                display_name = f"{product} Late Part {original_task_id}"
            elif task_type == 'Rework':
                display_name = f"{product} Rework {original_task_id}"
            else:
                display_name = f"{product} Task {original_task_id}"

            # Add criticality indicator to display name
            criticality_symbol = {
                'CRITICAL': '🔴',
                'BUFFER': '🟡',
                'FLEXIBLE': '🟢'
            }.get(criticality, '')

            display_name_with_criticality = f"{criticality_symbol} {display_name} [{criticality}]"

            priority_data.append({
                'task_instance_id': task_instance_id,
                'task_type': task_type,
                'display_name': display_name,
                'display_name_with_criticality': display_name_with_criticality,
                'criticality': criticality,  # Add this field
                'product_line': product,
                'original_task_id': original_task_id,
                'team': schedule['team'],
                'scheduled_start': schedule['start_time'],
                'scheduled_end': schedule['end_time'],
                'duration_minutes': schedule['duration'],
                'mechanics_required': schedule['mechanics_required'],
                'slack_hours': slack,
                'slack_days': slack / 24,  # Add days for easier interpretation
                'priority_score': self.calculate_task_priority(task_instance_id),
                'shift': schedule['shift']
            })

        priority_data.sort(key=lambda x: (x['scheduled_start'], x['slack_hours']))

        for i, task in enumerate(priority_data, 1):
            task['global_priority'] = i

        self.global_priority_list = priority_data
        return priority_data

    def calculate_lateness_metrics(self):
        """Calculate lateness metrics per product"""
        metrics = {}

        for product, delivery_date in self.delivery_dates.items():
            product_tasks = []
            for task_instance_id, schedule in self.task_schedule.items():
                if schedule.get('product') == product:
                    product_tasks.append(schedule)

            if product_tasks:
                last_task_end = max(task['end_time'] for task in product_tasks)
                lateness_days = (last_task_end - delivery_date).days

                task_type_counts = defaultdict(int)
                for task in product_tasks:
                    task_type_counts[task['task_type']] += 1

                metrics[product] = {
                    'delivery_date': delivery_date,
                    'projected_completion': last_task_end,
                    'lateness_days': lateness_days,
                    'on_time': lateness_days <= 0,
                    'total_tasks': len(product_tasks),
                    'task_breakdown': dict(task_type_counts)
                }
            else:
                metrics[product] = {
                    'delivery_date': delivery_date,
                    'projected_completion': None,
                    'lateness_days': 999999,
                    'on_time': False,
                    'total_tasks': 0,
                    'task_breakdown': {}
                }

        return metrics

    def scenario_2_minimize_makespan(self, min_mechanics=1, max_mechanics=100,
                                     min_quality=1, max_quality=50):
        """
        Scenario 2: Find uniform headcount that minimizes makespan using binary search
        """
        print("\n" + "=" * 80)
        print("SCENARIO 2: Minimize Makespan with Uniform Capacity")
        print("=" * 80)

        # Store original capacities
        original_team = self._original_team_capacity.copy()
        original_quality = self._original_quality_capacity.copy()

        best_makespan = float('inf')
        best_config = None
        best_metrics = None

        print(f"\nSearching uniform capacities:")
        print(f"  Mechanics: {min_mechanics} to {max_mechanics}")
        print(f"  Quality: {min_quality} to {max_quality}")

        # Binary search for mechanics
        mech_low, mech_high = min_mechanics, max_mechanics
        qual_low, qual_high = min_quality, max_quality

        iterations = 0
        max_iterations = 20  # Limit binary search iterations

        while iterations < max_iterations:
            iterations += 1

            # Try middle values
            mech_capacity = (mech_low + mech_high) // 2
            qual_capacity = (qual_low + qual_high) // 2

            print(f"\n  Testing: Mechanics={mech_capacity}, Quality={qual_capacity}")

            # Set uniform capacities
            for team in self.team_capacity:
                self.team_capacity[team] = mech_capacity
            for team in self.quality_team_capacity:
                self.quality_team_capacity[team] = qual_capacity

            # Clear previous schedule
            self.task_schedule = {}
            self._critical_path_cache = {}

            # Try to schedule
            try:
                self.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)

                scheduled_count = len(self.task_schedule)
                total_tasks = len(self.tasks)

                if scheduled_count == total_tasks:
                    # Complete schedule achieved
                    makespan = self.calculate_makespan()

                    print(f"    SUCCESS: Makespan={makespan} days")

                    if makespan < best_makespan:
                        best_makespan = makespan
                        best_config = {
                            'mechanics': mech_capacity,
                            'quality': qual_capacity
                        }
                        best_metrics = self.calculate_lateness_metrics()
                        print(f"    NEW BEST!")

                    # Try to reduce capacity
                    mech_high = mech_capacity - 1
                    qual_high = qual_capacity - 1

                else:
                    # Failed to schedule all tasks - need more capacity
                    print(f"    INCOMPLETE: Only {scheduled_count}/{total_tasks} scheduled")
                    mech_low = mech_capacity + 1
                    qual_low = qual_capacity + 1

            except Exception as e:
                print(f"    ERROR: {str(e)}")
                # Need more capacity
                mech_low = mech_capacity + 1
                qual_low = qual_capacity + 1

            # Check if search space exhausted
            if mech_low > mech_high or qual_low > qual_high:
                break

        # Restore original capacities
        for team, capacity in original_team.items():
            self.team_capacity[team] = capacity
        for team, capacity in original_quality.items():
            self.quality_team_capacity[team] = capacity

        if best_config:
            print(f"\n" + "=" * 80)
            print("SCENARIO 2 RESULTS")
            print("=" * 80)
            print(f"Optimal uniform capacity: Mechanics={best_config['mechanics']}, Quality={best_config['quality']}")
            print(f"Makespan: {best_makespan} days")

            return {
                'optimal_mechanics': best_config['mechanics'],
                'optimal_quality': best_config['quality'],
                'makespan': best_makespan,
                'metrics': best_metrics,
                'priority_list': [],  # Would need to regenerate
                'total_headcount': (best_config['mechanics'] * len(self.team_capacity) +
                                    best_config['quality'] * len(self.quality_team_capacity))
            }

        return None

    def calculate_makespan(self):
        """Calculate makespan in working days"""
        if not self.task_schedule:
            return 0

        scheduled_count = len(self.task_schedule)
        total_tasks = len(self.tasks)
        if scheduled_count < total_tasks:
            return 999999

        start_time = min(sched['start_time'] for sched in self.task_schedule.values())
        end_time = max(sched['end_time'] for sched in self.task_schedule.values())

        current = start_time.date()
        end_date = end_time.date()
        working_days = 0

        while current <= end_date:
            is_working = False
            for product in self.delivery_dates.keys():
                if self.is_working_day(datetime.combine(current, datetime.min.time()), product):
                    is_working = True
                    break
            if is_working:
                working_days += 1
            current += timedelta(days=1)

        return working_days

    def calculate_slack_time(self, task_id):
        """Calculate slack time for a task with overflow protection"""
        if task_id not in self.task_schedule:
            return float('inf')

        scheduled_start = self.task_schedule[task_id]['start_time']

        # Get product and delivery date if available
        product = self.tasks.get(task_id, {}).get('product')

        # For tasks without successors, use delivery date as constraint
        successors = self.get_successors(task_id)

        if not successors:
            # No successors - use product delivery date if available
            if product and product in self.delivery_dates:
                try:
                    delivery_date = pd.Timestamp(self.delivery_dates[product])
                    # Add a reasonable buffer to avoid overflow
                    if delivery_date.year > 2050:
                        return float('inf')

                    slack = (delivery_date - scheduled_start).total_seconds() / 3600
                    return max(0, slack)
                except (OverflowError, ValueError, AttributeError):
                    return float('inf')
            else:
                # No delivery constraint, effectively infinite slack
                return float('inf')

        # Calculate based on successors
        latest_start = None

        for successor_id in successors:
            if successor_id in self.task_schedule:
                successor_start = self.task_schedule[successor_id]['start_time']
                successor_task = self.tasks.get(successor_id, {})

                # Account for task duration
                task_duration_hours = self.tasks[task_id].get('duration', 0) / 60

                # Calculate when this task must start to not delay successor
                required_start = successor_start - pd.Timedelta(hours=task_duration_hours)

                if latest_start is None or required_start < latest_start:
                    latest_start = required_start

        # If no valid latest start found, use delivery date
        if latest_start is None:
            if product and product in self.delivery_dates:
                try:
                    latest_start = pd.Timestamp(self.delivery_dates[product])
                    # Safety check
                    if latest_start.year > 2050:
                        return float('inf')
                except (ValueError, AttributeError):
                    return float('inf')
            else:
                return float('inf')

        # Safety check for overflow
        try:
            # Check for unreasonable dates
            if latest_start.year > 2050 or scheduled_start.year > 2050:
                return float('inf')

            # Calculate slack
            slack_hours = (latest_start - scheduled_start).total_seconds() / 3600

            # Sanity check the result
            if abs(slack_hours) > 365 * 24:  # More than a year of slack seems wrong
                return float('inf')

            return max(0, slack_hours)

        except (OverflowError, ValueError, AttributeError) as e:
            if self.debug:
                print(f"[WARNING] Error calculating slack for task {task_id}: {str(e)}")
            return float('inf')

    def check_resource_conflicts(self):
        """Check for resource conflicts"""
        conflicts = []
        if not self.task_schedule:
            return conflicts

        team_tasks = defaultdict(list)
        for task_id, schedule in self.task_schedule.items():
            team_tasks[schedule['team']].append((task_id, schedule))

        for team, tasks in team_tasks.items():
            capacity = self.team_capacity.get(team, 0) or self.quality_team_capacity.get(team, 0)

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

    def schedule_tasks_with_critical_path_awareness(self, safety_buffer_days=2, silent_mode=False):
        """
        Schedule tasks with awareness of critical path and safety buffers
        CRITICAL tasks (slack < 2 days): Schedule ASAP
        BUFFER tasks (slack 2-5 days): Limited spreading
        FLEXIBLE tasks (slack > 5 days): Aggressive spreading
        """
        original_debug = self.debug
        if silent_mode:
            self.debug = False

        self.task_schedule = {}
        self._critical_path_cache = {}

        if not silent_mode and not self.validate_dag():
            raise ValueError("DAG validation failed!")

        dynamic_constraints = self.build_dynamic_dependencies()
        start_date = datetime(2025, 8, 22, 6, 0)

        constraints_by_second = defaultdict(list)
        constraints_by_first = defaultdict(list)

        for constraint in dynamic_constraints:
            constraints_by_second[constraint['Second']].append(constraint)
            constraints_by_first[constraint['First']].append(constraint)

        all_tasks = set(self.tasks.keys())
        total_tasks = len(all_tasks)
        ready_tasks = []

        if not silent_mode:
            print(f"\nStarting critical-path-aware scheduling for {total_tasks} task instances...")
            print(f"Safety buffer: {safety_buffer_days} days")

        # Find initially ready tasks
        tasks_with_incoming_constraints = set()
        tasks_with_outgoing_constraints = set()

        for constraint in dynamic_constraints:
            tasks_with_incoming_constraints.add(constraint['Second'])
            tasks_with_outgoing_constraints.add(constraint['First'])

        orphaned_tasks = all_tasks - tasks_with_incoming_constraints - tasks_with_outgoing_constraints

        if not silent_mode and orphaned_tasks:
            print(f"[DEBUG] Found {len(orphaned_tasks)} orphaned tasks with no constraints")

        for task in orphaned_tasks:
            priority = self.calculate_task_priority(task)
            heapq.heappush(ready_tasks, (priority, task))

        tasks_with_only_outgoing = tasks_with_outgoing_constraints - tasks_with_incoming_constraints
        for task in tasks_with_only_outgoing:
            priority = self.calculate_task_priority(task)
            heapq.heappush(ready_tasks, (priority, task))

        for task in tasks_with_incoming_constraints:
            constraints = constraints_by_second.get(task, [])
            has_blocking_constraints = False
            for c in constraints:
                rel = c['Relationship']
                if rel in ['Finish <= Start', 'Finish = Start', 'Finish <= Finish']:
                    has_blocking_constraints = True
                    break
            if not has_blocking_constraints:
                priority = self.calculate_task_priority(task)
                heapq.heappush(ready_tasks, (priority, task))

        if not silent_mode:
            print(f"[DEBUG] Initial ready queue has {len(ready_tasks)} tasks")

        scheduled_count = 0
        critical_count = 0
        buffer_count = 0
        flexible_count = 0
        max_iterations = total_tasks * 10
        iteration_count = 0
        failed_tasks = set()
        task_retry_counts = defaultdict(int)

        while ready_tasks and scheduled_count < total_tasks and iteration_count < max_iterations:
            iteration_count += 1

            if not ready_tasks:
                for task in all_tasks:
                    if task in self.task_schedule or task in failed_tasks:
                        continue

                    all_predecessors_scheduled = True
                    for constraint in constraints_by_second.get(task, []):
                        if constraint['First'] not in self.task_schedule:
                            all_predecessors_scheduled = False
                            break

                    if all_predecessors_scheduled:
                        priority = self.calculate_task_priority(task)
                        heapq.heappush(ready_tasks, (priority, task))

                if not ready_tasks:
                    if not silent_mode:
                        unscheduled = [t for t in all_tasks if t not in self.task_schedule and t not in failed_tasks]
                        print(f"[WARNING] No ready tasks but {len(unscheduled)} tasks remain unscheduled")
                    break

            priority, task_instance_id = heapq.heappop(ready_tasks)

            if task_retry_counts[task_instance_id] >= 3:
                if task_instance_id not in failed_tasks:
                    failed_tasks.add(task_instance_id)
                    if not silent_mode:
                        print(f"[ERROR] Task {task_instance_id} failed after 3 retries")
                continue

            task_info = self.tasks[task_instance_id]
            duration = task_info['duration']
            mechanics_needed = task_info['mechanics_required']
            is_quality = task_info['is_quality']
            task_type = task_info['task_type']
            product = task_info.get('product', 'Unknown')

            # Get the appropriate team for scheduling
            if is_quality:
                base_mechanic_team = task_info.get('team', '')
                quality_team = self.map_mechanic_to_quality_team(base_mechanic_team)
                team_for_scheduling = quality_team
                base_team = quality_team
            else:
                team_for_scheduling = task_info.get('team_skill', task_info['team'])
                # Extract base team
                if '(' in team_for_scheduling and ')' in team_for_scheduling:
                    base_team = team_for_scheduling.split(' (')[0].strip()
                else:
                    base_team = task_info.get('team', team_for_scheduling)

            earliest_start = start_date
            latest_start_constraint = None

            if task_instance_id in self.late_part_tasks:
                earliest_start = self.get_earliest_start_for_late_part(task_instance_id)

            for constraint in constraints_by_second.get(task_instance_id, []):
                first_task = constraint['First']
                relationship = constraint['Relationship']

                if first_task in self.task_schedule:
                    first_schedule = self.task_schedule[first_task]

                    if relationship == 'Finish <= Start':
                        constraint_time = first_schedule['end_time']
                    elif relationship == 'Finish = Start':
                        constraint_time = first_schedule['end_time']
                    elif relationship == 'Start <= Start' or relationship == 'Start = Start':
                        constraint_time = first_schedule['start_time']
                    elif relationship == 'Finish <= Finish':
                        constraint_time = first_schedule['end_time'] - timedelta(minutes=duration)
                    elif relationship == 'Start <= Finish':
                        constraint_time = first_schedule['start_time'] - timedelta(minutes=duration)
                    else:
                        constraint_time = first_schedule['end_time']

                    earliest_start = max(earliest_start, constraint_time)

                    if relationship == 'Start = Start':
                        latest_start_constraint = first_schedule['start_time']

            if latest_start_constraint:
                earliest_start = latest_start_constraint

            criticality = self.classify_task_criticality(task_instance_id)

            try:
                if criticality == 'CRITICAL':
                    critical_count += 1
                    scheduled_start, shift = self.get_next_working_time_with_capacity(
                        earliest_start, product, team_for_scheduling,
                        mechanics_needed, duration, is_quality
                    )

                elif criticality == 'BUFFER':
                    buffer_count += 1
                    scheduled_start, shift = self.find_best_slot_within_window(
                        task_instance_id, earliest_start,
                        max_lookahead_days=2,
                        safety_buffer_days=safety_buffer_days
                    )

                else:  # FLEXIBLE
                    flexible_count += 1
                    scheduled_start, shift = self.find_best_slot_within_window(
                        task_instance_id, earliest_start,
                        max_lookahead_days=5,
                        safety_buffer_days=safety_buffer_days
                    )

                scheduled_end = scheduled_start + timedelta(minutes=int(duration))

                self.task_schedule[task_instance_id] = {
                    'start_time': scheduled_start,
                    'end_time': scheduled_end,
                    'team': base_team,  # Base team for dashboard
                    'team_skill': team_for_scheduling,  # Full team+skill for capacity
                    'skill': task_info.get('skill'),  # Skill code
                    'product': product,
                    'duration': duration,
                    'mechanics_required': mechanics_needed,
                    'is_quality': is_quality,
                    'task_type': task_type,
                    'shift': shift,
                    'criticality': criticality,
                    'original_task_id': self.instance_to_original_task.get(task_instance_id)
                }

                scheduled_count += 1

                if not silent_mode and scheduled_count % 100 == 0:
                    print(f"  Scheduled {scheduled_count}/{total_tasks} tasks...")
                    print(f"    Critical: {critical_count}, Buffer: {buffer_count}, Flexible: {flexible_count}")

                for constraint in constraints_by_first.get(task_instance_id, []):
                    dependent = constraint['Second']
                    if dependent in self.task_schedule or dependent in failed_tasks:
                        continue

                    all_satisfied = True
                    for dep_constraint in constraints_by_second.get(dependent, []):
                        predecessor = dep_constraint['First']
                        if predecessor not in self.task_schedule:
                            all_satisfied = False
                            break

                    if all_satisfied and dependent not in [t[1] for t in ready_tasks]:
                        dep_priority = self.calculate_task_priority(dependent)
                        heapq.heappush(ready_tasks, (dep_priority, dependent))

            except Exception as e:
                if self.debug:
                    print(f"[ERROR] Failed to schedule {task_instance_id}: {str(e)}")
                task_retry_counts[task_instance_id] += 1
                if task_retry_counts[task_instance_id] < 3:
                    heapq.heappush(ready_tasks, (priority + 0.1, task_instance_id))
                else:
                    failed_tasks.add(task_instance_id)

        if not silent_mode:
            print(f"\n[DEBUG] Critical-path-aware scheduling complete!")
            print(f"  Scheduled: {scheduled_count}/{total_tasks} tasks")
            print(f"  Task Classification:")
            print(f"    Critical (ASAP): {critical_count}")
            print(f"    Buffer (limited spread): {buffer_count}")
            print(f"    Flexible (aggressive spread): {flexible_count}")
            if scheduled_count < total_tasks:
                unscheduled = total_tasks - scheduled_count
                print(f"[WARNING] {unscheduled} tasks could not be scheduled")

        self.debug = original_debug

    def classify_task_criticality(self, task_instance_id):
        """
        Classify task as CRITICAL, BUFFER, or FLEXIBLE based on slack time
        """
        task_info = self.tasks.get(task_instance_id, {})
        product = task_info.get('product')

        if not product or product not in self.delivery_dates:
            return 'FLEXIBLE'

        # Calculate total slack in days
        slack_hours = self.calculate_slack_time(task_instance_id)

        # Handle infinite slack
        if slack_hours == float('inf'):
            return 'FLEXIBLE'

        slack_days = slack_hours / 24

        # Classification thresholds
        if slack_days < 2:
            return 'CRITICAL'  # Must schedule ASAP
        elif slack_days < 5:
            return 'BUFFER'  # Some flexibility, but careful
        else:
            return 'FLEXIBLE'  # Can spread out safely

    def find_best_slot_within_window(self, task_instance_id, earliest_start,
                                     max_lookahead_days=3, safety_buffer_days=2):
        """
        Find best slot within safe window, considering both utilization and risk
        """
        task_info = self.tasks[task_instance_id]
        duration = task_info['duration']
        mechanics_needed = task_info['mechanics_required']
        is_quality = task_info['is_quality']
        product = task_info.get('product')

        # Get the appropriate team for scheduling
        if is_quality:
            base_team = task_info.get('team')
            team_for_scheduling = self.map_mechanic_to_quality_team(base_team)
        else:
            team_for_scheduling = task_info.get('team_skill', task_info['team'])

        max_lookahead = max_lookahead_days

        if product and product in self.delivery_dates:
            delivery_date = self.delivery_dates[product]
            critical_path_days = self.calculate_critical_path_length(task_instance_id) / (8 * 60)
            latest_safe_start = delivery_date - timedelta(days=critical_path_days + safety_buffer_days)

            days_to_latest = (latest_safe_start - earliest_start).days
            if days_to_latest > 0:
                max_lookahead = min(max_lookahead_days, days_to_latest)
            else:
                max_lookahead = 0

        if max_lookahead <= 0:
            return self.get_next_working_time_with_capacity(
                earliest_start, product, team_for_scheduling,
                mechanics_needed, duration, is_quality
            )

        best_score = float('inf')
        best_slot = None
        best_shift = None

        test_time = earliest_start
        end_window = earliest_start + timedelta(days=max_lookahead)

        slots_evaluated = 0
        while test_time < end_window and slots_evaluated < 50:
            if self.is_working_day(test_time, product):
                try:
                    slot_start, shift = self.get_next_working_time_with_capacity(
                        test_time, product, team_for_scheduling,
                        mechanics_needed, duration, is_quality
                    )

                    if slot_start < end_window:
                        day_util = self.calculate_day_utilization(team_for_scheduling, slot_start.date())

                        delay_days = (slot_start - earliest_start).total_seconds() / 86400

                        utilization_score = (100 - day_util) * 2
                        delay_penalty = delay_days * 20

                        total_score = utilization_score - delay_penalty
                        total_score = -total_score

                        if total_score < best_score:
                            best_score = total_score
                            best_slot = slot_start
                            best_shift = shift

                    slots_evaluated += 1

                except Exception:
                    pass

            test_time += timedelta(hours=4)

        if best_slot is None:
            return self.get_next_working_time_with_capacity(
                earliest_start, product, team_for_scheduling,
                mechanics_needed, duration, is_quality
            )

        return best_slot, best_shift

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

    def needs_product_changeover(self, team, new_product, current_time):
        """
        Check if team needs changeover time to switch products
        Returns (needs_changeover, last_product)
        """
        # Find the most recent task for this team before current_time
        team_tasks = [(tid, s) for tid, s in self.task_schedule.items()
                      if s['team'] == team and s['end_time'] <= current_time]

        if not team_tasks:
            return False, None  # First task for team, no changeover needed

        # Get the most recent task
        team_tasks.sort(key=lambda x: x[1]['end_time'], reverse=True)
        last_task_id, last_schedule = team_tasks[0]
        last_product = last_schedule.get('product')

        # Check if products are different
        if last_product and new_product and last_product != new_product:
            # Check if there's already enough gap (natural break)
            time_gap = (current_time - last_schedule['end_time']).total_seconds() / 60
            if time_gap < 15:  # Need changeover time
                return True, last_product

        return False, last_product

    def export_results(self, filename='scheduling_results.csv', scenario_name=''):
        """Export scheduling results to CSV"""
        if scenario_name:
            base = 'scheduling_results'
            ext = 'csv'
            if '.' in filename:
                base, ext = filename.rsplit('.', 1)
            filename = f"{base}_{scenario_name}.{ext}"

        if self.global_priority_list:
            df = pd.DataFrame(self.global_priority_list)
            df.to_csv(filename, index=False)
            print(f"Results exported to {filename}")

    def fill_schedule_gaps(self, target_utilization=80):
        """
        Post-process the schedule to fill gaps by moving flexible tasks
        """
        print(f"\n  Attempting to fill schedule gaps (target utilization: {target_utilization}%)...")

        # Find low-utilization days for each team
        team_daily_utils = defaultdict(dict)

        # Calculate daily utilization for all teams
        for team in list(self.team_capacity.keys()) + list(self.quality_team_capacity.keys()):
            team_tasks = [(tid, s) for tid, s in self.task_schedule.items() if s['team'] == team]

            if not team_tasks:
                continue

            min_date = min(s['start_time'].date() for _, s in team_tasks)
            max_date = max(s['end_time'].date() for _, s in team_tasks)

            current = min_date
            while current <= max_date:
                if self.is_working_day(datetime.combine(current, datetime.min.time()),
                                       list(self.delivery_dates.keys())[0]):
                    util = self.calculate_day_utilization(team, current)
                    team_daily_utils[team][current] = util
                current += timedelta(days=1)

        # Find tasks that can be moved
        moves_made = 0
        for team, daily_utils in team_daily_utils.items():
            low_days = [d for d, u in daily_utils.items() if u < target_utilization]
            high_days = [d for d, u in daily_utils.items() if u > target_utilization]

            if not low_days:
                continue

            print(f"    {team}: {len(low_days)} days below {target_utilization}% utilization")

            # Try to move tasks from high days to low days
            for high_day in high_days:
                for low_day in low_days:
                    if low_day >= high_day:  # Only move tasks earlier, not later
                        continue

                    # Find moveable tasks on high day
                    for task_id, schedule in list(self.task_schedule.items()):
                        if (schedule['team'] == team and
                                schedule['start_time'].date() == high_day):

                            # Check if we can move this task to low_day
                            new_start = datetime.combine(low_day, schedule['start_time'].time())

                            # Validate the move doesn't violate constraints
                            if self.can_reschedule_task(task_id, new_start):
                                # Move the task
                                old_start = schedule['start_time']
                                schedule['start_time'] = new_start
                                schedule['end_time'] = new_start + timedelta(minutes=schedule['duration'])
                                moves_made += 1

                                # Recalculate utilizations
                                team_daily_utils[team][low_day] = self.calculate_day_utilization(team, low_day)
                                team_daily_utils[team][high_day] = self.calculate_day_utilization(team, high_day)

                                if team_daily_utils[team][low_day] >= target_utilization:
                                    break

                    if team_daily_utils[team][low_day] >= target_utilization:
                        low_days.remove(low_day)
                        if not low_days:
                            break

        print(f"    Moved {moves_made} tasks to fill gaps")
        return moves_made

    def can_reschedule_task(self, task_id, new_start_time):
        """
        Check if a task can be rescheduled to a new time without violating constraints
        """
        task_info = self.tasks[task_id]
        duration = task_info['duration']
        new_end_time = new_start_time + timedelta(minutes=duration)

        # Get all constraints for this task
        dynamic_constraints = self.build_dynamic_dependencies()

        # Check predecessor constraints
        for constraint in dynamic_constraints:
            if constraint['Second'] == task_id:
                first_task = constraint['First']
                if first_task in self.task_schedule:
                    first_schedule = self.task_schedule[first_task]
                    temp_schedule = {
                        'start_time': new_start_time,
                        'end_time': new_end_time,
                        'duration': duration
                    }
                    is_satisfied, _, _ = self.check_constraint_satisfied(
                        first_schedule, temp_schedule, constraint['Relationship']
                    )
                    if not is_satisfied:
                        return False

        # Check successor constraints
        for constraint in dynamic_constraints:
            if constraint['First'] == task_id:
                second_task = constraint['Second']
                if second_task in self.task_schedule:
                    second_schedule = self.task_schedule[second_task]
                    temp_schedule = {
                        'start_time': new_start_time,
                        'end_time': new_end_time,
                        'duration': duration
                    }
                    is_satisfied, _, _ = self.check_constraint_satisfied(
                        temp_schedule, second_schedule, constraint['Relationship']
                    )
                    if not is_satisfied:
                        return False

        return True

    def scenario_1_csv_headcount(self):
        """Scenario 1: Use CSV-defined headcount"""
        print("\n" + "=" * 80)
        print("SCENARIO 1: Scheduling with CSV-defined Headcount")
        print("=" * 80)

        total_mechanics = sum(self.team_capacity.values())
        total_quality = sum(self.quality_team_capacity.values())

        print(f"\nTask Structure:")
        task_type_counts = defaultdict(int)
        for task_info in self.tasks.values():
            task_type_counts[task_info['task_type']] += 1

        for task_type, count in sorted(task_type_counts.items()):
            print(f"- {task_type}: {count} instances")

        print(f"- Total workforce: {total_mechanics + total_quality}")

        priority_list = self.generate_global_priority_list(allow_late_delivery=True)
        makespan = self.calculate_makespan()
        metrics = self.calculate_lateness_metrics()

        print(f"\nMakespan: {makespan} working days")
        print("\nDelivery Analysis:")
        print("-" * 80)

        total_late_days = 0
        for product, data in sorted(metrics.items()):
            if data['projected_completion'] is not None:
                status = "ON TIME" if data['on_time'] else f"LATE by {data['lateness_days']} days"
                print(f"{product}: Due {data['delivery_date'].strftime('%Y-%m-%d')}, "
                      f"Projected {data['projected_completion'].strftime('%Y-%m-%d')} - {status}")
                print(f"  Tasks: {data['total_tasks']} total - {data['task_breakdown']}")
                if data['lateness_days'] > 0:
                    total_late_days += data['lateness_days']
            else:
                print(f"{product}: UNSCHEDULED")

        return {
            'makespan': makespan,
            'metrics': metrics,
            'priority_list': priority_list,
            'team_capacities': dict(self.team_capacity),
            'quality_capacities': dict(self.quality_team_capacity),
            'total_late_days': total_late_days
        }

    def initialize_minimum_viable_capacity(self):
        """Initialize with minimum capacity needed for each team - ACTUALLY VIABLE"""
        config = {'mechanic': {}, 'quality': {}}

        # First pass: Find the maximum mechanics required by ANY task for each team
        max_requirements = {}

        for task_id, task_info in self.tasks.items():
            team = task_info.get('team_skill', task_info.get('team'))
            if not team:
                continue

            mechanics_required = task_info.get('mechanics_required', 1)

            # Track the maximum requirement for this team
            if team not in max_requirements:
                max_requirements[team] = mechanics_required
            else:
                max_requirements[team] = max(max_requirements[team], mechanics_required)

        # Second pass: Set capacities to AT LEAST the maximum required
        for team, max_required in max_requirements.items():
            if 'Quality' in team:
                config['quality'][team] = max_required  # Start at exactly what's needed
            else:
                config['mechanic'][team] = max_required  # Start at exactly what's needed

        # Ensure ALL teams from original capacity tables are included
        # (even if no tasks currently need them)
        for team in self._original_team_capacity:
            if team not in config['mechanic']:
                # Find if any task needs this team
                team_needed = False
                for task_info in self.tasks.values():
                    if task_info.get('team_skill') == team or task_info.get('team') == team:
                        team_needed = True
                        break

                if team_needed:
                    # Use the max requirement we found, or 1 as minimum
                    config['mechanic'][team] = max_requirements.get(team, 1)
                else:
                    config['mechanic'][team] = 0  # No tasks need this team currently

        for team in self._original_quality_capacity:
            if team not in config['quality']:
                # Find if any task needs this team
                team_needed = False
                for task_info in self.tasks.values():
                    if task_info.get('team_skill') == team or task_info.get('team') == team:
                        team_needed = True
                        break

                if team_needed:
                    config['quality'][team] = max_requirements.get(team, 1)
                else:
                    config['quality'][team] = 0  # No tasks need this team currently

        # Debug: Print what we're starting with
        if self.debug:
            print("\n[DEBUG] Initial minimum viable capacities:")
            print("  Teams needing capacity:")
            for team, req in sorted(max_requirements.items()):
                print(f"    {team}: needs at least {req} people")

        return config

    def generate_next_configuration(self, current_config, metrics, target_earliness, iteration):
        new_config = self.copy_configuration(current_config)

        # CRITICAL: Never go below actual task requirements
        min_requirements = self.calculate_minimum_team_requirements()

        # When adjusting capacity, respect minimums
        for team in new_config['mechanic']:
            min_needed = min_requirements.get(team, 1)
            new_config['mechanic'][team] = max(min_needed, new_config['mechanic'][team])

        for team in new_config['quality']:
            min_needed = min_requirements.get(team, 1)
            new_config['quality'][team] = max(min_needed, new_config['quality'][team])

        return new_config

    def apply_capacity_configuration(self, config):
        """Apply a capacity configuration to the scheduler"""
        for team, capacity in config['mechanic'].items():
            self.team_capacity[team] = capacity
        for team, capacity in config['quality'].items():
            self.quality_team_capacity[team] = capacity

    def evaluate_delivery_performance(self):
        """Evaluate how well the current schedule meets delivery targets"""
        # Use the actual lateness calculation method
        lateness_metrics = self.calculate_lateness_metrics()

        # Calculate key metrics
        lateness_values = []
        products_on_target = 0
        products_early = 0

        for product, metrics in lateness_metrics.items():
            if metrics['projected_completion'] is not None and metrics['lateness_days'] < 999999:
                lateness_values.append(metrics['lateness_days'])

                if metrics['lateness_days'] <= -1:  # At least 1 day early
                    products_on_target += 1
                if metrics['lateness_days'] < 0:  # Any amount early
                    products_early += 1

        # Calculate actual makespan
        makespan = self.calculate_makespan()

        # UPDATED UTILIZATION CALCULATION
        # For continuous flow production, use initial days rather than full makespan
        if makespan > 0 and makespan < 999999:
            # Option 1: Calculate based on first few days (continuous flow assumption)
            avg_utilization = self.calculate_initial_utilization(days_to_check=1)

            # Option 2: Alternative - use peak utilization
            # avg_utilization = self.calculate_peak_utilization()

            # Option 3: If you still want the old calculation as a fallback
            # total_work_minutes = sum(
            #     schedule['duration'] * schedule.get('mechanics_required', 1)
            #     for schedule in self.task_schedule.values()
            # )
            #
            # total_capacity_minutes = 0
            # for team, capacity in self.team_capacity.items():
            #     if capacity > 0:
            #         # Use actual shift hours, not hardcoded 8
            #         team_shifts = self.team_shifts.get(team, ['1st'])
            #         daily_minutes = 0
            #         for shift in team_shifts:
            #             shift_info = self.shift_hours.get(shift, {'start': '6:00', 'end': '14:30'})
            #             start_hour, start_min = self._parse_shift_time(shift_info['start'])
            #             end_hour, end_min = self._parse_shift_time(shift_info['end'])
            #             if shift == '3rd':
            #                 shift_minutes = ((24 - start_hour) * 60 - start_min) + (end_hour * 60 + end_min)
            #             else:
            #                 shift_minutes = (end_hour * 60 + end_min) - (start_hour * 60 + start_min)
            #             daily_minutes += shift_minutes
            #         total_capacity_minutes += capacity * daily_minutes * makespan
            #
            # for team, capacity in self.quality_team_capacity.items():
            #     if capacity > 0:
            #         team_shifts = self.quality_team_shifts.get(team, ['1st'])
            #         daily_minutes = 0
            #         for shift in team_shifts:
            #             shift_info = self.shift_hours.get(shift, {'start': '6:00', 'end': '14:30'})
            #             start_hour, start_min = self._parse_shift_time(shift_info['start'])
            #             end_hour, end_min = self._parse_shift_time(shift_info['end'])
            #             if shift == '3rd':
            #                 shift_minutes = ((24 - start_hour) * 60 - start_min) + (end_hour * 60 + end_min)
            #             else:
            #                 shift_minutes = (end_hour * 60 + end_min) - (start_hour * 60 + start_min)
            #             daily_minutes += shift_minutes
            #         total_capacity_minutes += capacity * daily_minutes * makespan
            #
            # avg_utilization = (total_work_minutes / total_capacity_minutes * 100) if total_capacity_minutes > 0 else 0
        else:
            avg_utilization = 0

        # Count workforce
        total_workforce = sum(self.team_capacity.values()) + sum(self.quality_team_capacity.values())

        # Return the ACTUAL max lateness from the metrics
        actual_max_lateness = max(lateness_values) if lateness_values else 999999

        return {
            'max_lateness': actual_max_lateness,
            'avg_lateness': sum(lateness_values) / len(lateness_values) if lateness_values else 999999,
            'min_lateness': min(lateness_values) if lateness_values else 999999,
            'products_on_target': products_on_target,
            'products_early': products_early,
            'total_workforce': total_workforce,
            'avg_utilization': avg_utilization,
            'scheduled_tasks': len(self.task_schedule),
            'total_tasks': len(self.tasks),
            'lateness_by_product': {p: m['lateness_days'] for p, m in lateness_metrics.items()},
            'makespan': makespan
        }

    def calculate_optimization_score(self, metrics, target_earliness):
        """Calculate optimization score (lower is better)"""
        # CRITICAL: Massive penalty for being far from target
        distance_from_target = abs(metrics['max_lateness'] - target_earliness)

        # Exponential penalty for distance from target
        earliness_penalty = distance_from_target ** 2 * 1000  # Quadratic penalty

        # Only care about workforce if we're close to target
        if distance_from_target <= 2:
            workforce_penalty = metrics['total_workforce'] * 10
        else:
            workforce_penalty = 0  # Don't care about workforce until we hit target

        # Utilization only matters if at target
        if distance_from_target <= 1:
            target_utilization = 75
            utilization_deviation = abs(metrics['avg_utilization'] - target_utilization)
            utilization_penalty = utilization_deviation * 5
        else:
            utilization_penalty = 0

        # Penalty for unscheduled tasks (always important)
        unscheduled_penalty = (metrics['total_tasks'] - metrics['scheduled_tasks']) * 5000

        total_score = earliness_penalty + workforce_penalty + utilization_penalty + unscheduled_penalty

        return total_score

    def identify_bottleneck_teams(self):
        """Identify teams that are bottlenecks (have unscheduled tasks)"""
        unscheduled_by_team = {}

        for task_id, task_info in self.tasks.items():
            if task_id not in self.task_schedule:
                team = task_info.get('team_skill', task_info.get('team'))
                if team:
                    unscheduled_by_team[team] = unscheduled_by_team.get(team, 0) + 1

        # Sort by number of unscheduled tasks
        sorted_teams = sorted(unscheduled_by_team.items(), key=lambda x: x[1], reverse=True)
        return [team for team, _ in sorted_teams]

    def identify_critical_path_teams(self):
        """Identify teams on the critical path for late products"""
        critical_teams = {}

        # Get lateness metrics
        lateness_metrics = self.calculate_lateness_metrics()

        # Find the latest product
        latest_product = None
        max_lateness = -float('inf')
        for product, metrics in lateness_metrics.items():
            if metrics['lateness_days'] > max_lateness and metrics['lateness_days'] < 999999:
                max_lateness = metrics['lateness_days']
                latest_product = product

        if not latest_product:
            return []

        # Find tasks for this product in the last few days of schedule
        for task_id, schedule in self.task_schedule.items():
            if schedule.get('product') == latest_product:
                # Check if task is near the end
                task_slack = self.calculate_slack_time(task_id)
                if task_slack < 48:  # Less than 2 days slack
                    team = schedule.get('team_skill', schedule.get('team'))
                    if team:
                        critical_teams[team] = critical_teams.get(team, 0) + 1

        # Sort by criticality
        sorted_teams = sorted(critical_teams.items(), key=lambda x: x[1], reverse=True)
        return [team for team, _ in sorted_teams]

    def identify_underutilized_teams(self):
        """Identify teams with low utilization"""
        utilization_by_team = {}

        makespan = self.calculate_makespan()
        if makespan == 0:
            return []

        for team, capacity in self.team_capacity.items():
            if capacity > 0:
                util = self.calculate_team_utilization(team, makespan)
                if util < 50:  # Less than 50% utilized
                    utilization_by_team[team] = util

        for team, capacity in self.quality_team_capacity.items():
            if capacity > 0:
                util = self.calculate_team_utilization(team, makespan)
                if util < 50:
                    utilization_by_team[team] = util

        # Sort by utilization (lowest first)
        sorted_teams = sorted(utilization_by_team.items(), key=lambda x: x[1])
        return [team for team, _ in sorted_teams]

    def calculate_team_utilization(self, team, makespan):
        """Calculate utilization for a specific team"""
        if makespan == 0:
            return 0

        capacity = self.team_capacity.get(team, 0) or self.quality_team_capacity.get(team, 0)
        if capacity == 0:
            return 0

        # Sum up work hours for this team
        total_work_minutes = 0
        for task_id, schedule in self.task_schedule.items():
            if schedule.get('team_skill', schedule.get('team')) == team:
                total_work_minutes += schedule['duration'] * schedule.get('mechanics_required', 1)

        # Available capacity (8 hours per day per person)
        available_minutes = capacity * 8 * 60 * makespan

        if available_minutes > 0:
            return (total_work_minutes / available_minutes) * 100
        return 0

    def calculate_discrete_utilization(self):
        """Calculate utilization for discrete product scheduling"""
        if not self.task_schedule:
            return 0

        makespan = self.calculate_makespan()
        if makespan == 0 or makespan >= 999999:
            return 0

        # Sum total work content
        total_work_minutes = sum(
            schedule['duration'] * schedule.get('mechanics_required', 1)
            for schedule in self.task_schedule.values()
        )

        # Sum total available capacity
        total_available_minutes = 0
        for team, capacity in self.team_capacity.items():
            if capacity > 0:
                total_available_minutes += capacity * 8 * 60 * makespan

        for team, capacity in self.quality_team_capacity.items():
            if capacity > 0:
                total_available_minutes += capacity * 8 * 60 * makespan

        if total_available_minutes > 0:
            return (total_work_minutes / total_available_minutes) * 100
        return 0

    def copy_configuration(self, config):
        """Create a deep copy of a configuration"""
        return {
            'mechanic': config['mechanic'].copy(),
            'quality': config['quality'].copy()
        }

    def increase_bottleneck_capacity(self, config):
        """Increase capacity for teams with unscheduled tasks and scheduling failures"""
        new_config = self.copy_configuration(config)

        # Find ALL teams that need more capacity
        unscheduled_by_team = {}
        max_required_by_team = {}
        task_count_by_team = {}

        # Analyze all tasks
        for task_id, task_info in self.tasks.items():
            team = task_info.get('team_skill', task_info.get('team'))
            if not team:
                continue

            mechanics_needed = task_info.get('mechanics_required', 1)

            # Track maximum requirement
            if team not in max_required_by_team:
                max_required_by_team[team] = mechanics_needed
            else:
                max_required_by_team[team] = max(max_required_by_team[team], mechanics_needed)

            # Count total tasks per team
            task_count_by_team[team] = task_count_by_team.get(team, 0) + 1

            # Count unscheduled tasks
            if task_id not in self.task_schedule:
                unscheduled_by_team[team] = unscheduled_by_team.get(team, 0) + 1

        # Calculate workload density for each team
        workload_density = {}
        for team, task_count in task_count_by_team.items():
            # Estimate total work minutes for this team
            total_minutes = 0
            for task_id, task_info in self.tasks.items():
                if task_info.get('team_skill', task_info.get('team')) == team:
                    total_minutes += task_info.get('duration', 60) * task_info.get('mechanics_required', 1)

            # Calculate how many people needed for this workload over 30 days
            available_minutes_per_person = 30 * 8 * 60  # 30 days * 8 hours * 60 minutes
            people_needed = total_minutes / available_minutes_per_person
            workload_density[team] = people_needed

        # Priority 1: Teams with unscheduled tasks
        teams_updated = 0

        for team, unscheduled_count in unscheduled_by_team.items():
            if unscheduled_count > 0:
                if 'Quality' in team:
                    current = new_config['quality'].get(team, 0)
                    min_needed = max_required_by_team.get(team, 1)

                    # Calculate ideal capacity based on workload
                    ideal_capacity = max(min_needed, int(workload_density.get(team, 1) * 1.5))  # 50% buffer

                    if current < ideal_capacity:
                        new_config['quality'][team] = ideal_capacity
                        teams_updated += 1
                        print(f"      {team}: {unscheduled_count} unscheduled, {current} -> {ideal_capacity} capacity")
                    elif unscheduled_count > 10:  # Many unscheduled despite having capacity
                        # Add more capacity
                        new_config['quality'][team] = current + max(2, unscheduled_count // 10)
                        teams_updated += 1
                        print(
                            f"      {team}: Still has {unscheduled_count} unscheduled, increasing {current} -> {current + max(2, unscheduled_count // 10)}")
                else:
                    current = new_config['mechanic'].get(team, 0)
                    min_needed = max_required_by_team.get(team, 1)

                    # Calculate ideal capacity based on workload
                    ideal_capacity = max(min_needed, int(workload_density.get(team, 1) * 1.5))

                    if current < ideal_capacity:
                        new_config['mechanic'][team] = ideal_capacity
                        teams_updated += 1
                        print(f"      {team}: {unscheduled_count} unscheduled, {current} -> {ideal_capacity} capacity")
                    elif unscheduled_count > 10:
                        new_config['mechanic'][team] = current + max(2, unscheduled_count // 10)
                        teams_updated += 1
                        print(
                            f"      {team}: Still has {unscheduled_count} unscheduled, increasing {current} -> {current + max(2, unscheduled_count // 10)}")

        # Priority 2: Ensure all teams meet minimum requirements
        for team, min_required in max_required_by_team.items():
            if 'Quality' in team:
                current = new_config['quality'].get(team, 0)
                if current < min_required:
                    new_config['quality'][team] = min_required + 1  # Buffer
                    teams_updated += 1
                    print(f"      {team}: Increasing to minimum required {min_required + 1}")
            else:
                current = new_config['mechanic'].get(team, 0)
                if current < min_required:
                    new_config['mechanic'][team] = min_required + 1
                    teams_updated += 1
                    print(f"      {team}: Increasing to minimum required {min_required + 1}")

        # Priority 3: Special handling for known bottlenecks
        quality_bottlenecks = ['Quality Team 1', 'Quality Team 4', 'Quality Team 7', 'Quality Team 10']

        for team in quality_bottlenecks:
            if team in task_count_by_team:
                task_count = task_count_by_team[team]
                if task_count > 50:  # Heavy workload
                    current = new_config['quality'].get(team, 0)
                    # These teams handle many tasks, ensure adequate capacity
                    min_capacity = max(5, int(workload_density.get(team, 3) * 1.2))
                    if current < min_capacity:
                        new_config['quality'][team] = min_capacity
                        print(f"      {team}: High-workload team, ensuring minimum {min_capacity} capacity")

        # Priority 4: If still having failures after multiple iterations, increase all bottleneck teams
        if hasattr(self, 'consecutive_failures') and self.consecutive_failures > 2:
            print(f"      Multiple scheduling failures detected, increasing all bottleneck teams")
            # Sort teams by unscheduled count
            sorted_bottlenecks = sorted(unscheduled_by_team.items(), key=lambda x: x[1], reverse=True)

            for team, unscheduled_count in sorted_bottlenecks[:10]:  # Top 10 bottlenecks
                if unscheduled_count > 0:
                    if 'Quality' in team:
                        current = new_config['quality'].get(team, 0)
                        # Aggressive increase for persistent failures
                        new_config['quality'][team] = max(current + 3, int(workload_density.get(team, 2) * 2))
                        print(f"      {team}: Aggressive increase due to repeated failures")
                    else:
                        current = new_config['mechanic'].get(team, 0)
                        new_config['mechanic'][team] = max(current + 3, int(workload_density.get(team, 2) * 2))
                        print(f"      {team}: Aggressive increase due to repeated failures")

        if teams_updated > 0:
            print(f"      Total teams updated: {teams_updated}")

        return new_config

    def increase_critical_capacity(self, config, metrics):
        """Increase capacity for critical path teams"""
        new_config = self.copy_configuration(config)

        # Simple approach: increase all teams slightly
        for team in config['mechanic']:
            new_config['mechanic'][team] = config['mechanic'][team] + 1
        for team in config['quality']:
            new_config['quality'][team] = config['quality'][team] + 1

        return new_config

    def reduce_capacity_carefully(self, config, metrics):
        """Reduce capacity when too early"""
        new_config = self.copy_configuration(config)

        # Find teams with lowest utilization
        team_utils = self.calculate_team_utilizations()

        # Reduce lowest utilized teams
        for team, util in sorted(team_utils.items(), key=lambda x: x[1])[:2]:
            if util < 50:  # Only reduce if very underutilized
                if 'Quality' in team:
                    if new_config['quality'].get(team, 0) > 2:
                        new_config['quality'][team] -= 1
                else:
                    if new_config['mechanic'].get(team, 0) > 2:
                        new_config['mechanic'][team] -= 1

        return new_config

    def reduce_lowest_utilization_team(self, config):
        """Reduce capacity of least utilized team"""
        new_config = self.copy_configuration(config)

        team_utils = self.calculate_team_utilizations()
        if team_utils:
            lowest_team = min(team_utils.items(), key=lambda x: x[1])[0]

            if 'Quality' in lowest_team:
                if new_config['quality'].get(lowest_team, 0) > 1:
                    new_config['quality'][lowest_team] -= 1
            else:
                if new_config['mechanic'].get(lowest_team, 0) > 1:
                    new_config['mechanic'][lowest_team] -= 1

        return new_config

    def increase_highest_utilization_team(self, config):
        """Increase capacity of most utilized team"""
        new_config = self.copy_configuration(config)

        team_utils = self.calculate_team_utilizations()
        if team_utils:
            highest_team = max(team_utils.items(), key=lambda x: x[1])[0]

            if 'Quality' in highest_team:
                new_config['quality'][highest_team] = new_config['quality'].get(highest_team, 0) + 1
            else:
                new_config['mechanic'][highest_team] = new_config['mechanic'].get(highest_team, 0) + 1

        return new_config

    def make_small_adjustment(self, config, iteration):
        """Make small random adjustment for exploration"""
        new_config = self.copy_configuration(config)

        import random
        random.seed(iteration)

        # Pick a random team to adjust
        if random.random() < 0.5 and config['mechanic']:
            team = random.choice(list(config['mechanic'].keys()))
            if random.random() < 0.5 and new_config['mechanic'][team] > 1:
                new_config['mechanic'][team] -= 1
            else:
                new_config['mechanic'][team] += 1
        elif config['quality']:
            team = random.choice(list(config['quality'].keys()))
            if random.random() < 0.5 and new_config['quality'][team] > 1:
                new_config['quality'][team] -= 1
            else:
                new_config['quality'][team] += 1

        return new_config

    def calculate_team_utilizations(self):
        """Calculate utilization for each team"""
        if not self.task_schedule:
            return {}

        makespan = self.calculate_makespan()
        if makespan == 0:
            return {}

        utilizations = {}

        for team, capacity in self.team_capacity.items():
            if capacity > 0:
                util = self.calculate_team_utilization(team, makespan)
                utilizations[team] = util

        for team, capacity in self.quality_team_capacity.items():
            if capacity > 0:
                util = self.calculate_team_utilization(team, makespan)
                utilizations[team] = util

        return utilizations

    def scenario_3_smart_optimization(self, target_earliness=-1, max_iterations=500):
        """
        Scenario 3: Optimize team capacities to achieve target delivery (1 day early)
        Start with adequate capacity and optimize to hit target precisely
        """
        print("\n" + "=" * 80)
        print("SCENARIO 3: Smart Optimization for Target Delivery")
        print("=" * 80)
        print(f"Target: All products {abs(target_earliness)} day(s) early")

        # Store originals
        original_team = self._original_team_capacity.copy()
        original_quality = self._original_quality_capacity.copy()

        # Store target for use in helper methods
        self.target_earliness = target_earliness

        # CRITICAL: Find minimum requirements first
        min_requirements = {}
        for task_id, task_info in self.tasks.items():
            team = task_info.get('team_skill', task_info.get('team'))
            if team:
                mechanics_needed = task_info.get('mechanics_required', 1)
                min_requirements[team] = max(min_requirements.get(team, 0), mechanics_needed)

        # Initialize with at least minimum requirements
        current_config = {
            'mechanic': {},
            'quality': {}
        }

        # Set initial capacities to meet ALL requirements
        for team in original_team:
            # Start with at least what's needed, or moderate default
            min_needed = min_requirements.get(team, 2)
            current_config['mechanic'][team] = max(min_needed + 2, 5)  # Buffer above minimum

        for team in original_quality:
            min_needed = min_requirements.get(team, 1)
            current_config['quality'][team] = max(min_needed + 1, 3)  # Buffer above minimum

        print(f"\nMinimum requirements found:")
        sample_reqs = list(min_requirements.items())[:5]
        for team, req in sample_reqs:
            print(f"  {team}: needs at least {req} people")

        print(f"\nStarting configuration:")
        print(f"  Mechanic teams: {len(current_config['mechanic'])} teams")
        print(f"  Quality teams: {len(current_config['quality'])} teams")
        print(
            f"  Initial workforce: {sum(current_config['mechanic'].values()) + sum(current_config['quality'].values())}")

        best_config = None
        best_score = float('inf')
        best_metrics = None

        # Track optimization progress
        iteration_history = []
        no_improvement_count = 0
        stuck_count = 0
        last_max_lateness = None
        consecutive_failures = 0

        for iteration in range(max_iterations):
            # Apply current configuration
            self.apply_capacity_configuration(current_config)

            # Clear caches and schedule
            self.task_schedule = {}
            self._critical_path_cache = {}

            # Schedule silently
            try:
                self.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)
                consecutive_failures = 0  # Reset failure counter
            except Exception as e:
                consecutive_failures += 1
                print(f"  Iteration {iteration}: Scheduling failed ({consecutive_failures} consecutive failures)")

                # Ensure all teams meet minimum requirements
                for team, min_req in min_requirements.items():
                    if 'Quality' in team:
                        if current_config['quality'].get(team, 0) < min_req:
                            current_config['quality'][team] = min_req + 1
                    else:
                        if current_config['mechanic'].get(team, 0) < min_req:
                            current_config['mechanic'][team] = min_req + 1

                # If repeated failures, increase all capacities
                if consecutive_failures > 3:
                    for team in current_config['mechanic']:
                        current_config['mechanic'][team] += 2
                    for team in current_config['quality']:
                        current_config['quality'][team] += 1
                    consecutive_failures = 0
                continue

            # Evaluate performance
            metrics = self.evaluate_delivery_performance()

            # Check if we're stuck at the same lateness
            if last_max_lateness == metrics['max_lateness']:
                stuck_count += 1
            else:
                stuck_count = 0
            last_max_lateness = metrics['max_lateness']

            # Calculate optimization score
            score = self.calculate_optimization_score(metrics, target_earliness)

            # Track progress
            iteration_history.append({
                'iteration': iteration,
                'score': score,
                'max_lateness': metrics['max_lateness'],
                'total_workforce': metrics['total_workforce'],
                'scheduled_tasks': metrics['scheduled_tasks'],
                'avg_utilization': metrics['avg_utilization']
            })

            # Check if this is the best configuration so far
            if score < best_score and metrics['scheduled_tasks'] == metrics['total_tasks']:
                improvement = best_score - score
                best_score = score
                best_config = self.copy_configuration(current_config)
                best_metrics = metrics.copy()
                no_improvement_count = 0

                print(f"\n  Iteration {iteration}: NEW BEST!")
                print(f"    Max lateness: {metrics['max_lateness']} days (target: {target_earliness})")
                print(f"    Distance from target: {abs(metrics['max_lateness'] - target_earliness)} days")
                print(f"    Total workforce: {metrics['total_workforce']}")
                print(f"    Utilization: {metrics['avg_utilization']:.1f}%")
                print(f"    Tasks scheduled: {metrics['scheduled_tasks']}/{metrics['total_tasks']}")

                # Only stop if we're actually at target with good utilization
                if abs(metrics['max_lateness'] - target_earliness) <= 1:
                    print(f"\n  ✓ TARGET ACHIEVED! Max lateness: {metrics['max_lateness']} days")
                    if metrics['avg_utilization'] > 60:
                        print(f"    With good utilization: {metrics['avg_utilization']:.1f}%")
                        break
                    else:
                        print(
                            f"    But utilization low: {metrics['avg_utilization']:.1f}%, continuing to optimize workforce...")
            else:
                no_improvement_count += 1

            # Print progress every 10 iterations
            if iteration % 10 == 0:
                distance = abs(metrics['max_lateness'] - target_earliness)
                print(f"  Iteration {iteration}: Lateness={metrics['max_lateness']} (distance={distance}), "
                      f"Workforce={metrics['total_workforce']}, Scheduled={metrics['scheduled_tasks']}/{metrics['total_tasks']}")

            # If stuck for too long, make bigger changes
            if stuck_count > 15:
                print(f"    Stuck at {metrics['max_lateness']} days for {stuck_count} iterations")
                distance_from_target = abs(metrics['max_lateness'] - target_earliness)

                if distance_from_target > 20:
                    # Way off target - make big changes
                    print(f"    Making large adjustments (distance: {distance_from_target} days)")
                    if metrics['max_lateness'] < target_earliness:
                        # Too early - cut capacity significantly
                        for team in current_config['mechanic']:
                            if current_config['mechanic'][team] > min_requirements.get(team, 1):
                                current_config['mechanic'][team] = max(
                                    min_requirements.get(team, 1),
                                    int(current_config['mechanic'][team] * 0.7)
                                )
                        for team in current_config['quality']:
                            if current_config['quality'][team] > min_requirements.get(team, 1):
                                current_config['quality'][team] = max(
                                    min_requirements.get(team, 1),
                                    int(current_config['quality'][team] * 0.7)
                                )
                    else:
                        # Too late - increase capacity
                        for team in current_config['mechanic']:
                            current_config['mechanic'][team] += 3
                        for team in current_config['quality']:
                            current_config['quality'][team] += 2
                else:
                    # Make random changes to escape local optimum
                    current_config = self.make_large_adjustment(current_config, iteration)

                stuck_count = 0
                continue

            # Don't terminate early unless actually at target
            if no_improvement_count >= 100:
                distance = abs(best_metrics.get('max_lateness', 999) - target_earliness) if best_metrics else 999
                if distance <= 2:
                    print(f"\n  No improvement for 100 iterations, accepting solution {distance} days from target")
                    break
                else:
                    print(f"\n  No improvement for 100 iterations but still {distance} days from target")
                    # Make drastic changes
                    no_improvement_count = 0
                    if best_metrics and best_metrics['max_lateness'] < target_earliness - 10:
                        # Still way too early - cut more aggressively
                        print(f"    Cutting capacity aggressively")
                        for team in current_config['mechanic']:
                            current_config['mechanic'][team] = max(
                                min_requirements.get(team, 1),
                                current_config['mechanic'][team] // 2
                            )
                        for team in current_config['quality']:
                            current_config['quality'][team] = max(
                                min_requirements.get(team, 1),
                                current_config['quality'][team] // 2
                            )

            # Adjust configuration based on current performance
            if metrics['scheduled_tasks'] < metrics['total_tasks']:
                # Not all tasks scheduled - increase capacity where needed
                print(f"    Only {metrics['scheduled_tasks']}/{metrics['total_tasks']} scheduled, increasing capacity")
                current_config = self.increase_bottleneck_capacity(current_config)

            elif abs(metrics['max_lateness'] - target_earliness) > 20:
                # Very far from target - make aggressive changes
                days_off = abs(metrics['max_lateness'] - target_earliness)
                print(f"    {days_off} days from target, making aggressive adjustments")

                if metrics['max_lateness'] < target_earliness:
                    # Too early - reduce capacity aggressively
                    reduction_factor = min(0.5, days_off / 50)  # More aggressive for larger gaps
                    for team in current_config['mechanic']:
                        reduction = int(current_config['mechanic'][team] * reduction_factor)
                        current_config['mechanic'][team] = max(
                            min_requirements.get(team, 1),
                            current_config['mechanic'][team] - max(1, reduction)
                        )
                    for team in current_config['quality']:
                        reduction = int(current_config['quality'][team] * reduction_factor)
                        current_config['quality'][team] = max(
                            min_requirements.get(team, 1),
                            current_config['quality'][team] - max(1, reduction)
                        )
                else:
                    # Too late - increase capacity
                    for team in current_config['mechanic']:
                        current_config['mechanic'][team] += 2
                    for team in current_config['quality']:
                        current_config['quality'][team] += 1

            elif abs(metrics['max_lateness'] - target_earliness) > 5:
                # Moderately far from target
                print(
                    f"    {abs(metrics['max_lateness'] - target_earliness)} days from target, making moderate adjustments")

                if metrics['max_lateness'] < target_earliness:
                    # Too early - reduce capacity moderately
                    for team in current_config['mechanic']:
                        if current_config['mechanic'][team] > min_requirements.get(team, 1) + 1:
                            current_config['mechanic'][team] -= 1
                    for team in current_config['quality']:
                        if current_config['quality'][team] > min_requirements.get(team, 1):
                            current_config['quality'][team] = max(
                                min_requirements.get(team, 1),
                                current_config['quality'][team] - 1
                            )
                else:
                    # Too late - increase capacity moderately
                    current_config = self.increase_critical_capacity(current_config, metrics)

            else:
                # Close to target - fine tune
                if metrics['avg_utilization'] < 50:
                    # Low utilization - try to reduce workforce
                    current_config = self.reduce_lowest_utilization_team(current_config)
                elif metrics['avg_utilization'] > 85:
                    # High utilization - might need more capacity
                    current_config = self.increase_highest_utilization_team(current_config)
                else:
                    # Make small adjustments
                    current_config = self.make_small_adjustment(current_config, iteration)

        # Restore original capacities
        for team, capacity in original_team.items():
            self.team_capacity[team] = capacity
        for team, capacity in original_quality.items():
            self.quality_team_capacity[team] = capacity

        if best_config:
            print(f"\n" + "=" * 80)
            print("OPTIMIZATION COMPLETE")
            print("=" * 80)
            print(f"Best configuration found:")
            print(f"  Max lateness: {best_metrics['max_lateness']} days")
            print(f"  Target was: {target_earliness} days")
            print(f"  Distance from target: {abs(best_metrics['max_lateness'] - target_earliness)} days")
            print(f"  Total workforce: {best_metrics['total_workforce']}")
            print(f"  Average utilization: {best_metrics['avg_utilization']:.1f}%")
            print(f"  Tasks scheduled: {best_metrics['scheduled_tasks']}/{best_metrics['total_tasks']}")

            return {
                'config': best_config,
                'total_workforce': best_metrics['total_workforce'],
                'max_lateness': best_metrics['max_lateness'],
                'metrics': best_metrics,
                'perfect_count': best_metrics.get('products_on_target', 0),
                'good_count': best_metrics.get('products_early', 0),
                'acceptable_count': best_metrics.get('products_on_target', 0),
                'avg_utilization': best_metrics['avg_utilization'],
                'utilization_variance': 0
            }

        return None

    def reduce_capacity_aggressively(self, config, metrics):
        """Aggressively reduce capacity when way too early"""
        new_config = self.copy_configuration(config)

        # Calculate how much too early we are
        target = self.target_earliness if hasattr(self, 'target_earliness') else -1
        days_too_early = abs(metrics['max_lateness'] - target)

        if days_too_early > 20:  # Way too early (e.g., -26 vs -1)
            # Reduce all teams by 30-40%
            for team in new_config['mechanic']:
                current = new_config['mechanic'][team]
                reduction = max(1, int(current * 0.35))
                new_config['mechanic'][team] = max(1, current - reduction)
            for team in new_config['quality']:
                current = new_config['quality'][team]
                reduction = max(1, int(current * 0.35))
                new_config['quality'][team] = max(1, current - reduction)
            print(f"      Aggressive reduction: 35% across all teams")

        elif days_too_early > 10:  # Moderately too early
            # Reduce all teams by 20%
            for team in new_config['mechanic']:
                current = new_config['mechanic'][team]
                reduction = max(1, int(current * 0.20))
                new_config['mechanic'][team] = max(1, current - reduction)
            for team in new_config['quality']:
                current = new_config['quality'][team]
                reduction = max(1, int(current * 0.20))
                new_config['quality'][team] = max(1, current - reduction)
            print(f"      Moderate reduction: 20% across all teams")

        elif days_too_early > 5:  # Somewhat too early
            # Reduce by 2-3 people per team
            for team in new_config['mechanic']:
                if new_config['mechanic'][team] > 3:
                    new_config['mechanic'][team] -= 2
            for team in new_config['quality']:
                if new_config['quality'][team] > 2:
                    new_config['quality'][team] -= 1
            print(f"      Standard reduction: -2 mechanics, -1 quality per team")

        else:  # Slightly too early
            # Reduce by 1 person per team
            for team in new_config['mechanic']:
                if new_config['mechanic'][team] > 2:
                    new_config['mechanic'][team] -= 1
            for team in new_config['quality']:
                if new_config['quality'][team] > 1:
                    new_config['quality'][team] -= 1
            print(f"      Small reduction: -1 person per team")

        return new_config

    def make_large_adjustment(self, config, iteration):
        """Make larger random adjustments when stuck"""
        new_config = self.copy_configuration(config)

        import random
        random.seed(iteration)

        # Adjust multiple teams at once
        num_teams_to_adjust = max(3, len(config['mechanic']) // 4)

        # Randomly select teams to adjust
        mechanic_teams = random.sample(list(config['mechanic'].keys()),
                                       min(num_teams_to_adjust, len(config['mechanic'])))
        quality_teams = random.sample(list(config['quality'].keys()),
                                      min(num_teams_to_adjust // 2, len(config['quality'])))

        for team in mechanic_teams:
            if random.random() < 0.5 and new_config['mechanic'][team] > 2:
                new_config['mechanic'][team] -= 2
            else:
                new_config['mechanic'][team] += 2

        for team in quality_teams:
            if random.random() < 0.5 and new_config['quality'][team] > 1:
                new_config['quality'][team] -= 1
            else:
                new_config['quality'][team] += 1

        return new_config

    def validate_schedule_comprehensive(self, verbose=True):
        """Comprehensive validation of the generated schedule"""
        validation_results = {
            'is_valid': True,
            'total_tasks': len(self.tasks),
            'scheduled_tasks': len(self.task_schedule),
            'errors': [],
            'warnings': [],
            'stats': {}
        }

        if verbose:
            print("\n" + "=" * 80)
            print("SCHEDULE VALIDATION")
            print("=" * 80)

        unscheduled_tasks = []
        for task_id in self.tasks:
            if task_id not in self.task_schedule:
                unscheduled_tasks.append(task_id)

        if unscheduled_tasks:
            validation_results['is_valid'] = False
            validation_results['errors'].append(f"INCOMPLETE: {len(unscheduled_tasks)} tasks not scheduled")
            if verbose:
                print(f"\n❌ {len(unscheduled_tasks)} tasks NOT scheduled")
        else:
            if verbose:
                print(f"\n✓ All {len(self.tasks)} tasks scheduled")

        return validation_results

    def debug_scheduling_failure(self, task_id):
        """Debug why a specific task cannot be scheduled"""
        print(f"\n" + "=" * 80)
        print(f"DEBUGGING: {task_id}")
        print("=" * 80)

        if task_id not in self.tasks:
            print(f"❌ Task {task_id} does not exist!")
            return

        task_info = self.tasks[task_id]
        print(f"\nTask Details:")
        print(f"  Type: {task_info['task_type']}")
        print(f"  Team: {task_info.get('team', 'NONE')}")
        print(f"  Mechanics Required: {task_info['mechanics_required']}")
        print(f"  Duration: {task_info['duration']} minutes")

        team = task_info.get('team')
        if team:
            capacity = self.team_capacity.get(team, 0) or self.quality_team_capacity.get(team, 0)
            print(f"\nTeam Capacity:")
            print(f"  {team}: {capacity} people")

            if task_info['mechanics_required'] > capacity:
                print(f"  ❌ IMPOSSIBLE: Task needs {task_info['mechanics_required']} but team has {capacity}")

    def diagnose_scheduling_issues(self):
        """Diagnose why tasks aren't being scheduled"""
        print("\n" + "=" * 80)
        print("SCHEDULING DIAGNOSTIC REPORT")
        print("=" * 80)

        # Count tasks by status
        total_tasks = len(self.tasks)
        scheduled_tasks = len(self.task_schedule)
        unscheduled_tasks = total_tasks - scheduled_tasks

        print(f"\nTask Scheduling Summary:")
        print(f"  Total tasks: {total_tasks}")
        print(f"  Scheduled: {scheduled_tasks}")
        print(f"  Unscheduled: {unscheduled_tasks}")

        # Identify unscheduled tasks
        unscheduled = []
        for task_id in self.tasks:
            if task_id not in self.task_schedule:
                unscheduled.append(task_id)

        # Analyze unscheduled tasks by type
        unscheduled_by_type = defaultdict(list)
        unscheduled_by_product = defaultdict(list)
        unscheduled_by_team = defaultdict(list)

        for task_id in unscheduled:
            task_info = self.tasks[task_id]
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
                task_info = self.tasks[ex]
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
        dynamic_constraints = self.build_dynamic_dependencies()

        # Find tasks with unsatisfied dependencies
        blocked_tasks = []
        for task_id in unscheduled:
            predecessors = []
            for constraint in dynamic_constraints:
                if constraint['Second'] == task_id:
                    first_task = constraint['First']
                    if first_task not in self.task_schedule:
                        predecessors.append(first_task)

            if predecessors:
                blocked_tasks.append((task_id, predecessors))

        print(f"\nTasks blocked by unscheduled predecessors: {len(blocked_tasks)}")
        for task_id, preds in blocked_tasks[:5]:  # Show first 5
            print(f"  {task_id} blocked by: {preds[:3]}")  # Show first 3 blockers

        # Check for circular dependencies
        print("\n[CIRCULAR DEPENDENCY CHECK]")

        def find_cycles():
            graph = defaultdict(set)
            for constraint in dynamic_constraints:
                graph[constraint['First']].add(constraint['Second'])

            visited = set()
            rec_stack = set()
            cycles = []

            def has_cycle(node, path):
                visited.add(node)
                rec_stack.add(node)
                path.append(node)

                for neighbor in graph.get(node, []):
                    if neighbor not in visited:
                        if has_cycle(neighbor, path):
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
                    has_cycle(node, [])

            return cycles

        cycles = find_cycles()
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
        for task_id in self.tasks:
            if task_id not in tasks_in_constraints:
                orphaned.append(task_id)

        print(f"  Tasks not in any constraints: {len(orphaned)}")
        for task_id in orphaned[:5]:
            task_info = self.tasks[task_id]
            print(f"    - {task_id}: type={task_info.get('task_type')}, "
                  f"product={task_info.get('product')}")

        # Check team availability
        print("\n[TEAM CAPACITY CHECK]")
        for team in sorted(set(self.team_capacity.keys()) | set(self.quality_team_capacity.keys())):
            capacity = self.team_capacity.get(team, 0) or self.quality_team_capacity.get(team, 0)

            # Count tasks needing this team
            tasks_needing_team = [t for t in self.tasks if self.tasks[t].get('team') == team]
            scheduled_for_team = [t for t in self.task_schedule if self.task_schedule[t].get('team') == team]

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

    def run_diagnostic(self):
        """Run diagnostic after scheduling attempt"""
        print("\nRunning scheduling diagnostic...")

        # First, try to schedule with high verbosity
        self.schedule_tasks(allow_late_delivery=True, silent_mode=False)

        # Then run the diagnostic
        diagnostic_results = self.diagnose_scheduling_issues()

        # Additional specific checks
        print("\n[QUALITY INSPECTION MAPPING CHECK]")
        qi_without_team = 0
        qi_with_team = 0

        for task_id, task_info in self.tasks.items():
            if task_info.get('is_quality', False):
                if task_info.get('team'):
                    qi_with_team += 1
                else:
                    qi_without_team += 1
                    print(f"  QI without team: {task_id}")

        print(f"  Quality inspections with teams: {qi_with_team}")
        print(f"  Quality inspections without teams: {qi_without_team}")

        return diagnostic_results

    def print_delivery_analysis(self, scenario_name=""):
        """Print detailed delivery analysis for all products"""
        metrics = self.calculate_lateness_metrics()

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
        print(f"Target in Scenario 3: {self.scenario_3_target if hasattr(self, 'scenario_3_target') else 'N/A'}")
        print("=" * 80)

        return max_lateness

    def identify_product_bottlenecks(self, product):
        """Identify which teams are bottlenecks for a specific product"""
        bottleneck_teams = defaultdict(int)

        # Find all tasks for this product
        product_tasks = [(tid, info) for tid, info in self.task_schedule.items()
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

    def calculate_average_utilization_properly(self):
        """Calculate utilization for first day only (continuous flow assumption)"""
        if not self.task_schedule:
            return 0

        # Find the first working day
        start_date = min(s['start_time'].date() for s in self.task_schedule.values())

        # Calculate total work on day 1
        day1_work_minutes = 0
        for task_id, schedule in self.task_schedule.items():
            if schedule['start_time'].date() == start_date:
                day1_work_minutes += schedule['duration'] * schedule.get('mechanics_required', 1)

        # Calculate total available capacity for day 1
        day1_capacity_minutes = 0

        # Add mechanic team capacity
        for team, capacity in self.team_capacity.items():
            if capacity > 0:
                shifts = self.team_shifts.get(team, ['1st'])
                for shift in shifts:
                    shift_info = self.shift_hours.get(shift, {'start': '6:00', 'end': '14:30'})
                    start_hour, start_min = self._parse_shift_time(shift_info['start'])
                    end_hour, end_min = self._parse_shift_time(shift_info['end'])

                    if shift == '3rd':  # Crosses midnight
                        shift_minutes = ((24 - start_hour) * 60 - start_min) + (end_hour * 60 + end_min)
                    else:
                        shift_minutes = (end_hour * 60 + end_min) - (start_hour * 60 + start_min)

                    day1_capacity_minutes += shift_minutes * capacity

        # Add quality team capacity
        for team, capacity in self.quality_team_capacity.items():
            if capacity > 0:
                shifts = self.quality_team_shifts.get(team, ['1st'])
                for shift in shifts:
                    shift_info = self.shift_hours.get(shift, {'start': '6:00', 'end': '14:30'})
                    start_hour, start_min = self._parse_shift_time(shift_info['start'])
                    end_hour, end_min = self._parse_shift_time(shift_info['end'])

                    if shift == '3rd':
                        shift_minutes = ((24 - start_hour) * 60 - start_min) + (end_hour * 60 + end_min)
                    else:
                        shift_minutes = (end_hour * 60 + end_min) - (start_hour * 60 + start_min)

                    day1_capacity_minutes += shift_minutes * capacity

        if day1_capacity_minutes > 0:
            return (day1_work_minutes / day1_capacity_minutes) * 100
        return 0

    def debug_scheduling_slot_search(self, task_id, verbose=True):
        """Debug why a specific task cannot find a scheduling slot"""
        if task_id not in self.tasks:
            print(f"Task {task_id} not found")
            return

        task_info = self.tasks[task_id]
        duration = task_info['duration']
        mechanics_needed = task_info['mechanics_required']
        is_quality = task_info['is_quality']
        product = task_info.get('product')

        # Get team
        if is_quality:
            team = self.map_mechanic_to_quality_team(task_info.get('team'))
            capacity = self.quality_team_capacity.get(team, 0)
            shifts = self.quality_team_shifts.get(team, [])
        else:
            team = task_info.get('team_skill', task_info.get('team'))
            capacity = self.team_capacity.get(team, 0)
            shifts = self.team_shifts.get(team, [])

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

            if not self.is_working_day(test_date, product):
                print(f"    Not a working day (holiday/weekend)")
                continue

            for shift in shifts:
                shift_info = self.shift_hours.get(shift)
                if not shift_info:
                    print(f"    Shift {shift}: No hours defined")
                    continue

                print(f"    Shift {shift}: {shift_info['start']} - {shift_info['end']}")

                # Calculate actual shift window
                start_hour, start_min = self._parse_shift_time(shift_info['start'])
                end_hour, end_min = self._parse_shift_time(shift_info['end'])

                if shift == '3rd':
                    shift_start = test_date.replace(hour=23, minute=0)
                    shift_end = (test_date + timedelta(days=1)).replace(hour=6, minute=0)
                else:
                    shift_start = test_date.replace(hour=start_hour, minute=start_min)
                    shift_end = test_date.replace(hour=end_hour, minute=end_min)

                # Check capacity usage in this shift
                conflicts = 0
                conflicting_tasks = []

                for scheduled_id, schedule in self.task_schedule.items():
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

    def schedule_with_level_loading_and_criticality(self, task_criticality,
                                                    target_utilization=82.5,
                                                    max_delay_days=None,
                                                    silent_mode=True):
        """
        Schedule tasks with criticality awareness and level-loading
        This version properly handles dependencies and constraints
        """
        if max_delay_days is None:
            max_delay_days = {'CRITICAL': 0, 'BUFFER': 1, 'FLEXIBLE': 3}

        original_debug = self.debug
        if silent_mode:
            self.debug = False

        self.task_schedule = {}
        self._critical_path_cache = {}

        if not self.validate_dag():
            raise ValueError("DAG validation failed!")

        dynamic_constraints = self.build_dynamic_dependencies()
        start_date = datetime(2025, 8, 22, 6, 0)

        # Build constraint lookups
        constraints_by_second = defaultdict(list)
        constraints_by_first = defaultdict(list)

        for constraint in dynamic_constraints:
            constraints_by_second[constraint['Second']].append(constraint)
            constraints_by_first[constraint['First']].append(constraint)

        all_tasks = set(self.tasks.keys())
        total_tasks = len(all_tasks)
        ready_tasks = []

        if not silent_mode:
            print(f"  Scheduling {total_tasks} tasks with criticality-aware level loading...")

        # Find initially ready tasks
        tasks_with_incoming = set()
        tasks_with_outgoing = set()

        for constraint in dynamic_constraints:
            tasks_with_incoming.add(constraint['Second'])
            tasks_with_outgoing.add(constraint['First'])

        # Add orphaned tasks
        orphaned = all_tasks - tasks_with_incoming - tasks_with_outgoing
        for task in orphaned:
            priority = self.calculate_task_priority(task)
            heapq.heappush(ready_tasks, (priority, task))

        # Add tasks with only outgoing constraints
        for task in tasks_with_outgoing - tasks_with_incoming:
            priority = self.calculate_task_priority(task)
            heapq.heappush(ready_tasks, (priority, task))

        scheduled_count = 0
        critical_scheduled = 0
        buffer_scheduled = 0
        flexible_scheduled = 0

        while ready_tasks and scheduled_count < total_tasks:
            priority, task_instance_id = heapq.heappop(ready_tasks)

            if task_instance_id in self.task_schedule:
                continue

            task_info = self.tasks[task_instance_id]
            duration = task_info['duration']
            mechanics_needed = task_info['mechanics_required']
            is_quality = task_info['is_quality']
            product = task_info.get('product', 'Unknown')

            # Get task criticality
            criticality = task_criticality.get(task_instance_id, 'FLEXIBLE')

            # Get team for scheduling
            if is_quality:
                base_team = task_info.get('team', '')
                team_for_scheduling = self.map_mechanic_to_quality_team(base_team)
                base_team_for_schedule = team_for_scheduling
            else:
                team_for_scheduling = task_info.get('team_skill', task_info['team'])
                if '(' in team_for_scheduling and ')' in team_for_scheduling:
                    base_team_for_schedule = team_for_scheduling.split(' (')[0].strip()
                else:
                    base_team_for_schedule = task_info.get('team', team_for_scheduling)

            # Calculate earliest start based on constraints
            earliest_start = start_date

            # Handle late parts
            if task_instance_id in self.late_part_tasks:
                earliest_start = self.get_earliest_start_for_late_part(task_instance_id)

            # Check predecessor constraints
            for constraint in constraints_by_second.get(task_instance_id, []):
                first_task = constraint['First']
                if first_task in self.task_schedule:
                    first_schedule = self.task_schedule[first_task]
                    relationship = constraint['Relationship']

                    if relationship == 'Finish <= Start' or relationship == 'Finish = Start':
                        constraint_time = first_schedule['end_time']
                    elif relationship == 'Start <= Start':
                        constraint_time = first_schedule['start_time']
                    elif relationship == 'Finish <= Finish':
                        constraint_time = first_schedule['end_time'] - timedelta(minutes=duration)
                    else:
                        constraint_time = first_schedule['end_time']

                    earliest_start = max(earliest_start, constraint_time)

            # NOW APPLY LEVEL-LOADING LOGIC BASED ON CRITICALITY
            best_start = None
            best_shift = None

            if criticality == 'CRITICAL':
                # Critical tasks: Schedule ASAP
                best_start, best_shift = self.get_next_working_time_with_capacity(
                    earliest_start, product, team_for_scheduling,
                    mechanics_needed, duration, is_quality
                )
                critical_scheduled += 1

            elif criticality == 'BUFFER':
                # Buffer tasks: Limited lookahead for better utilization
                best_start, best_shift = self.find_level_loaded_slot(
                    earliest_start, product, team_for_scheduling,
                    mechanics_needed, duration, is_quality,
                    max_lookahead_days=max_delay_days['BUFFER'],
                    target_utilization=target_utilization
                )
                buffer_scheduled += 1

            else:  # FLEXIBLE
                # Flexible tasks: Aggressive lookahead to fill gaps
                best_start, best_shift = self.find_level_loaded_slot(
                    earliest_start, product, team_for_scheduling,
                    mechanics_needed, duration, is_quality,
                    max_lookahead_days=max_delay_days['FLEXIBLE'],
                    target_utilization=target_utilization
                )
                flexible_scheduled += 1

            # Schedule the task
            scheduled_end = best_start + timedelta(minutes=int(duration))

            self.task_schedule[task_instance_id] = {
                'start_time': best_start,
                'end_time': scheduled_end,
                'team': base_team_for_schedule,
                'team_skill': team_for_scheduling,
                'skill': task_info.get('skill'),
                'product': product,
                'duration': duration,
                'mechanics_required': mechanics_needed,
                'is_quality': is_quality,
                'task_type': task_info.get('task_type'),
                'shift': best_shift,
                'criticality': criticality,
                'original_task_id': self.instance_to_original_task.get(task_instance_id)
            }

            scheduled_count += 1

            # Add dependent tasks to ready queue
            for constraint in constraints_by_first.get(task_instance_id, []):
                dependent = constraint['Second']
                if dependent in self.task_schedule:
                    continue

                # Check if all predecessors are scheduled
                all_satisfied = True
                for dep_constraint in constraints_by_second.get(dependent, []):
                    if dep_constraint['First'] not in self.task_schedule:
                        all_satisfied = False
                        break

                if all_satisfied and dependent not in [t[1] for t in ready_tasks]:
                    dep_priority = self.calculate_task_priority(dependent)
                    heapq.heappush(ready_tasks, (dep_priority, dependent))

        if not silent_mode:
            print(
                f"    Scheduled: {critical_scheduled} critical, {buffer_scheduled} buffer, {flexible_scheduled} flexible")

        self.debug = original_debug

    def find_level_loaded_slot(self, earliest_start, product, team,
                               mechanics_needed, duration, is_quality,
                               max_lookahead_days=3, target_utilization=82.5):
        """
        Find the best slot within lookahead window to balance utilization
        """
        best_score = float('inf')
        best_start = None
        best_shift = None

        # Sample time slots within the lookahead window
        test_time = earliest_start
        end_window = earliest_start + timedelta(days=max_lookahead_days)

        slots_tested = 0
        max_slots_to_test = 20  # Limit for performance

        while test_time < end_window and slots_tested < max_slots_to_test:
            if self.is_working_day(test_time, product):
                try:
                    # Get the next available slot starting from test_time
                    slot_start, shift = self.get_next_working_time_with_capacity(
                        test_time, product, team, mechanics_needed, duration, is_quality
                    )

                    # Only consider if within our window
                    if slot_start < end_window:
                        # Calculate utilization for this day
                        day_util = self.calculate_day_utilization(team, slot_start.date())

                        # Calculate score (lower is better)
                        util_deviation = abs(day_util - target_utilization)
                        delay_penalty = (slot_start - earliest_start).total_seconds() / 86400 * 10

                        # Bonus for filling low-utilization days
                        if day_util < 60:
                            fill_bonus = -20  # Negative score is good
                        else:
                            fill_bonus = 0

                        total_score = util_deviation + delay_penalty + fill_bonus

                        if total_score < best_score:
                            best_score = total_score
                            best_start = slot_start
                            best_shift = shift

                    slots_tested += 1

                except Exception:
                    pass

            # Move to next test slot (every 4 hours)
            test_time += timedelta(hours=4)

        # If no good slot found, use ASAP
        if best_start is None:
            best_start, best_shift = self.get_next_working_time_with_capacity(
                earliest_start, product, team, mechanics_needed, duration, is_quality
            )

        return best_start, best_shift

    def identify_task_relationships(self):
        """Identify tasks with no predecessors/successors"""
        all_tasks = set(range(1, 101))  # Assuming tasks 1-100

        first_tasks = set()
        second_tasks = set()

        for constraint in self.precedence_constraints:
            first_tasks.add(constraint['First'])
            second_tasks.add(constraint['Second'])

        no_predecessors = all_tasks - second_tasks
        no_successors = all_tasks - first_tasks
        orphaned = all_tasks - first_tasks - second_tasks

        print(f"Tasks with no predecessors: {sorted(no_predecessors)}")
        print(f"Tasks with no successors: {sorted(no_successors)}")
        print(f"Orphaned tasks (no relationships): {sorted(orphaned)}")

    def calculate_average_utilization(self):
        """Calculate average utilization across all teams"""
        if not self.task_schedule:
            return 0

        makespan = self.calculate_makespan()
        if makespan == 0 or makespan >= 999999:
            return 0

        total_utilization = 0
        team_count = 0

        # Calculate for mechanic teams
        for team, capacity in self.team_capacity.items():
            if capacity > 0:
                util = self.calculate_day_utilization(team, datetime.now().date())
                if util > 0:
                    total_utilization += util
                    team_count += 1

        # Calculate for quality teams
        for team, capacity in self.quality_team_capacity.items():
            if capacity > 0:
                util = self.calculate_day_utilization(team, datetime.now().date())
                if util > 0:
                    total_utilization += util
                    team_count += 1

        return total_utilization / team_count if team_count > 0 else 0

    def schedule_single_task(self, task_id, allow_delay=True, preferred_start=None):
        """Schedule a single task with optional preferred start time"""
        if task_id not in self.tasks:
            return False

        task_info = self.tasks[task_id]
        duration = task_info['duration']
        mechanics_needed = task_info['mechanics_required']
        is_quality = task_info['is_quality']
        product = task_info.get('product')

        # Get team
        if is_quality:
            base_team = task_info.get('team')
            team = self.map_mechanic_to_quality_team(base_team)
        else:
            team = task_info.get('team_skill', task_info['team'])

        # Determine start time
        if preferred_start:
            start_time = preferred_start
        else:
            start_time = datetime(2025, 8, 22, 6, 0)

        # Find next available slot
        scheduled_start, shift = self.get_next_working_time_with_capacity(
            start_time, product, team, mechanics_needed, duration, is_quality
        )

        scheduled_end = scheduled_start + timedelta(minutes=duration)

        # Store schedule
        self.task_schedule[task_id] = {
            'start_time': scheduled_start,
            'end_time': scheduled_end,
            'team': task_info.get('team'),
            'team_skill': team,
            'product': product,
            'duration': duration,
            'mechanics_required': mechanics_needed,
            'is_quality': is_quality,
            'task_type': task_info.get('task_type'),
            'shift': shift
        }

        return True

    def find_best_time_for_task(self, task_id, max_lookahead_days=3, target_utilization=82.5):
        """Find the best time to schedule a task to balance utilization"""
        task_info = self.tasks[task_id]
        duration = task_info['duration']
        mechanics_needed = task_info['mechanics_required']
        is_quality = task_info['is_quality']
        product = task_info.get('product')

        # Get team
        if is_quality:
            base_team = task_info.get('team')
            team = self.map_mechanic_to_quality_team(base_team)
        else:
            team = task_info.get('team_skill', task_info['team'])

        earliest_start = datetime(2025, 8, 22, 6, 0)

        # Check constraints
        dynamic_constraints = self.build_dynamic_dependencies()
        for constraint in dynamic_constraints:
            if constraint['Second'] == task_id:
                first_task = constraint['First']
                if first_task in self.task_schedule:
                    first_end = self.task_schedule[first_task]['end_time']
                    earliest_start = max(earliest_start, first_end)

        best_time = earliest_start
        best_score = float('inf')

        # Try different start times
        test_time = earliest_start
        end_window = earliest_start + timedelta(days=max_lookahead_days)

        while test_time < end_window:
            if self.is_working_day(test_time, product):
                # Calculate utilization if we schedule here
                test_date = test_time.date()
                current_util = self.calculate_day_utilization(team, test_date)

                # Score: distance from target utilization
                util_score = abs(current_util - target_utilization)

                # Penalty for delaying
                delay_days = (test_time - earliest_start).days
                delay_penalty = delay_days * 10

                total_score = util_score + delay_penalty

                if total_score < best_score:
                    best_score = total_score
                    best_time = test_time

            test_time += timedelta(hours=8)

        return best_time

    def scenario_3_simulated_annealing(self, target_earliness=-1, max_iterations=500,
                                       initial_temp=100, cooling_rate=0.95):
        """Use simulated annealing to optimize for target delivery date"""
        import random
        import math

        print("\n" + "=" * 80)
        print("SCENARIO 3: Simulated Annealing Optimization")
        print("=" * 80)
        print(f"Target: All products {abs(target_earliness)} day(s) early")

        # Store originals
        original_team = self._original_team_capacity.copy()
        original_quality = self._original_quality_capacity.copy()

        # Store the target for use in other methods
        self.target_earliness = target_earliness

        # Initialize with moderate capacity
        current_config = self.initialize_moderate_capacity()
        best_config = self.copy_configuration(current_config)
        best_score = float('inf')
        best_metrics = None

        temperature = initial_temp
        no_improvement = 0

        for iteration in range(max_iterations):
            # Apply configuration and schedule
            self.apply_capacity_configuration(current_config)
            self.task_schedule = {}
            self._critical_path_cache = {}

            try:
                self.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)
                metrics = self.evaluate_delivery_performance()

                # Calculate score with heavy weight on distance from target
                distance = abs(metrics['max_lateness'] - target_earliness)
                current_score = (distance ** 2) * 1000  # Quadratic penalty for distance

                if metrics['scheduled_tasks'] < metrics['total_tasks']:
                    current_score += (metrics['total_tasks'] - metrics['scheduled_tasks']) * 5000

                # Add workforce penalty only if close to target
                if distance <= 2:
                    current_score += metrics['total_workforce'] * 5

                # Check if we should accept this solution
                if current_score < best_score:
                    delta = best_score - current_score
                    best_score = current_score
                    best_config = self.copy_configuration(current_config)
                    best_metrics = metrics.copy()
                    no_improvement = 0

                    print(f"\n  Iteration {iteration}: NEW BEST!")
                    print(f"    Lateness: {metrics['max_lateness']} (target: {target_earliness})")
                    print(f"    Distance: {distance} days")
                    print(f"    Workforce: {metrics['total_workforce']}")

                    if distance == 0:
                        print(f"  ✓ TARGET ACHIEVED!")
                        if iteration > 50:  # Give some time for refinement
                            break
                else:
                    # Probabilistic acceptance of worse solution
                    delta = current_score - best_score
                    probability = math.exp(-delta / temperature) if temperature > 0 else 0

                    if random.random() < probability:
                        # Accept worse solution
                        no_improvement = 0
                    else:
                        # Reject - revert to best
                        current_config = self.copy_configuration(best_config)
                        no_improvement += 1

                # Make neighbor solution
                if metrics['scheduled_tasks'] < metrics['total_tasks']:
                    # Focus on fixing unscheduled tasks
                    current_config = self.fix_unscheduled_tasks(current_config)
                else:
                    # Adjust based on distance from target
                    if metrics['max_lateness'] < target_earliness:
                        # Too early - reduce capacity
                        current_config = self.reduce_random_teams(current_config,
                                                                  min(5, abs(distance)))
                    elif metrics['max_lateness'] > target_earliness:
                        # Too late - increase capacity
                        current_config = self.increase_random_teams(current_config,
                                                                    min(5, distance + 1))
                    else:
                        # At target - fine tune workforce
                        current_config = self.fine_tune_workforce(current_config)

                # Cool down
                temperature *= cooling_rate

                # Reheat if stuck
                if no_improvement > 30:
                    temperature = initial_temp * 0.5  # Reheat to half
                    no_improvement = 0
                    print(f"  Reheating at iteration {iteration}")

            except Exception as e:
                print(f"  Iteration {iteration}: Scheduling failed - adjusting capacity")
                current_config = self.increase_all_capacity(current_config, 2)

        # Restore original capacities
        for team, capacity in original_team.items():
            self.team_capacity[team] = capacity
        for team, capacity in original_quality.items():
            self.quality_team_capacity[team] = capacity

        return {
            'config': best_config,
            'metrics': best_metrics,
            'total_workforce': best_metrics['total_workforce'] if best_metrics else None,
            'max_lateness': best_metrics['max_lateness'] if best_metrics else None
        }

    def validate_schedulability(self):
        """Validate that all tasks CAN theoretically be scheduled"""
        print("\n" + "=" * 80)
        print("SCHEDULABILITY VALIDATION")
        print("=" * 80)

        issues = []
        warnings = []

        # Check 1: Team capacity vs task requirements
        for task_id, task_info in self.tasks.items():
            team = task_info.get('team_skill', task_info.get('team'))
            mechanics_needed = task_info.get('mechanics_required', 1)

            if task_info.get('is_quality'):
                capacity = self.quality_team_capacity.get(team, 0)
            else:
                capacity = self.team_capacity.get(team, 0)

            if capacity == 0:
                issues.append(f"Task {task_id} requires team '{team}' which has 0 capacity")
            elif mechanics_needed > capacity:
                issues.append(f"Task {task_id} needs {mechanics_needed} people but '{team}' only has {capacity}")

        # Check 2: Circular dependencies
        cycles = self.find_dependency_cycles()
        if cycles:
            for cycle in cycles:
                issues.append(f"Circular dependency: {' -> '.join(cycle)}")

        # Check 3: Total workload vs theoretical capacity
        total_work_minutes = sum(
            task['duration'] * task.get('mechanics_required', 1)
            for task in self.tasks.values()
        )

        # Calculate total available minutes over reasonable timeframe (30 days)
        total_capacity_minutes = 0
        for team, capacity in self.team_capacity.items():
            if capacity > 0:
                total_capacity_minutes += capacity * 8 * 60 * 30  # 30 days
        for team, capacity in self.quality_team_capacity.items():
            if capacity > 0:
                total_capacity_minutes += capacity * 8 * 60 * 30

        if total_work_minutes > total_capacity_minutes:
            issues.append(
                f"Total work ({total_work_minutes} min) exceeds 30-day capacity ({total_capacity_minutes} min)")

        # Check 4: Tasks with missing team assignments
        for task_id, task_info in self.tasks.items():
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

    def find_dependency_cycles(self):
        """Find circular dependencies in the task graph"""
        graph = defaultdict(set)
        dynamic_constraints = self.build_dynamic_dependencies()

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

    def initialize_moderate_capacity(self):
        """Initialize with moderate capacity for all teams"""
        config = {'mechanic': {}, 'quality': {}}

        # Find minimum requirements
        min_requirements = self.calculate_minimum_team_requirements()

        # Set moderate capacity (minimum + buffer)
        for team in self._original_team_capacity:
            min_needed = min_requirements.get(team, 2)
            config['mechanic'][team] = max(min_needed + 2, 5)

        for team in self._original_quality_capacity:
            min_needed = min_requirements.get(team, 1)
            config['quality'][team] = max(min_needed + 1, 3)

        return config

    def fix_unscheduled_tasks(self, config):
        """Increase capacity for teams with unscheduled tasks"""
        new_config = self.copy_configuration(config)

        for task_id, task_info in self.tasks.items():
            if task_id not in self.task_schedule:
                team = task_info.get('team_skill', task_info.get('team'))
                if team:
                    if 'Quality' in team:
                        new_config['quality'][team] = new_config['quality'].get(team, 0) + 1
                    else:
                        new_config['mechanic'][team] = new_config['mechanic'].get(team, 0) + 1

        return new_config

    def reduce_random_teams(self, config, amount):
        """Randomly reduce capacity of some teams"""
        import random
        new_config = self.copy_configuration(config)

        teams_to_reduce = random.sample(list(config['mechanic'].keys()),
                                        min(amount, len(config['mechanic'])))
        for team in teams_to_reduce:
            if new_config['mechanic'][team] > 2:
                new_config['mechanic'][team] -= 1

        return new_config

    def increase_random_teams(self, config, amount):
        """Randomly increase capacity of some teams"""
        import random
        new_config = self.copy_configuration(config)

        teams_to_increase = random.sample(list(config['mechanic'].keys()),
                                          min(amount, len(config['mechanic'])))
        for team in teams_to_increase:
            new_config['mechanic'][team] += 1

        return new_config

    def fine_tune_workforce(self, config):
        """Fine tune by adjusting lowest utilized teams"""
        new_config = self.copy_configuration(config)

        # Simple adjustment - reduce a random underutilized team
        team_utils = self.calculate_team_utilizations()
        if team_utils:
            # Find teams with low utilization
            low_util_teams = [t for t, u in team_utils.items() if u < 50]
            if low_util_teams:
                import random
                team = random.choice(low_util_teams)
                if 'Quality' in team and new_config['quality'].get(team, 0) > 1:
                    new_config['quality'][team] -= 1
                elif team in new_config['mechanic'] and new_config['mechanic'][team] > 2:
                    new_config['mechanic'][team] -= 1

        return new_config

    def increase_all_capacity(self, config, amount):
        """Increase all team capacities by fixed amount"""
        new_config = self.copy_configuration(config)

        for team in new_config['mechanic']:
            new_config['mechanic'][team] += amount
        for team in new_config['quality']:
            new_config['quality'][team] += amount

        return new_config

# Replace the MAIN block (starting from if __name__ == "__main__":) with this corrected version
        # ========== SCENARIO 3: OPTIMIZE FOR TARGET DELIVERY ==========
        # ========== SCENARIO 3: OPTIMIZE FOR TARGET DELIVERY ==========
        print("\n" + "=" * 80)
        print("SCENARIO 3: OPTIMIZE FOR TARGET DELIVERY")
        print("=" * 80)

        # Store target for use in other methods
        scheduler.scenario_3_target = args.target_earliness

        scenario3_optimization = scheduler.scenario_3_smart_optimization(
            target_earliness=args.target_earliness,
            max_iterations=300
        )

        if scenario3_optimization:
            # IMPORTANT: Clear everything and re-run with optimal configuration
            print("\nApplying optimal configuration and generating final schedule...")

            # Reset to original capacities first
            for team, capacity in scheduler._original_team_capacity.items():
                scheduler.team_capacity[team] = capacity
            for team, capacity in scheduler._original_quality_capacity.items():
                scheduler.quality_team_capacity[team] = capacity

            # Now apply the optimal configuration
            scheduler.apply_capacity_configuration(scenario3_optimization['config'])

            # Clear all scheduling data
            scheduler.task_schedule = {}
            scheduler._critical_path_cache = {}
            scheduler._dynamic_constraints_cache = None

            # Re-run complete scheduling
            scheduler.schedule_tasks(allow_late_delivery=True, silent_mode=True)

            # Verify we scheduled all tasks
            if len(scheduler.task_schedule) != len(scheduler.tasks):
                print(f"[WARNING] Only scheduled {len(scheduler.task_schedule)}/{len(scheduler.tasks)} tasks")

            # Generate priority list
            scheduler.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)

            # Get the ACTUAL metrics from the new schedule
            actual_metrics = scheduler.calculate_lateness_metrics()

            # Verify metrics
            product_count = 0
            for product, pm in actual_metrics.items():
                if isinstance(pm, dict) and 'lateness_days' in pm and pm['lateness_days'] < 999999:
                    product_count += 1

            print(f"  Generated metrics for {product_count} products")

            # Store results - DO NOT include old optimization metrics
            results['scenario3'] = {
                'makespan': scheduler.calculate_makespan(),
                'metrics': actual_metrics,  # Only the actual scheduling metrics
                'total_workforce': scenario3_optimization['total_workforce'],
                'avg_utilization': scheduler.calculate_average_utilization_properly()
            }

            scheduler.export_results(filename='scheduling_results_scenario3.csv')
            max_lateness_s3 = scheduler.print_delivery_analysis(
                "SCENARIO 3")  # Replace the MAIN block (starting from if __name__ == "__main__":) with this corrected version


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Production Scheduler')
    parser.add_argument('--csv', type=str, default='scheduling_data.csv',
                        help='Path to CSV file')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug output')
    parser.add_argument('--target-earliness', type=int, default=-1,
                        help='Target earliness in days (negative = early, e.g., -1 = 1 day early)')
    parser.add_argument('--validate', action='store_true',
                        help='Run validation after each scenario')
    parser.add_argument('--diagnose', action='store_true',
                        help='Run diagnostic to identify scheduling issues')
    parser.add_argument('--level-loading', action='store_true',
                        help='Use level-loading in Scenario 1')
    parser.add_argument('--critical-aware', action='store_true',
                        help='Use critical-path-aware scheduling in Scenario 1')

    args = parser.parse_args()

    try:
        # Initialize scheduler
        scheduler = ProductionScheduler(args.csv, debug=args.debug)

        # MUST BE FIRST: Load data
        print("Loading data from CSV...")
        scheduler.load_data_from_csv()

        print("\n" + "=" * 80)
        print("DATA LOADED SUCCESSFULLY")
        print("=" * 80)

        # Print summary statistics
        task_type_counts = defaultdict(int)
        product_counts = defaultdict(int)

        for task_info in scheduler.tasks.values():
            task_type_counts[task_info['task_type']] += 1
            if 'product' in task_info and task_info['product']:
                product_counts[task_info['product']] += 1

        print(f"Total task instances: {len(scheduler.tasks)}")
        for task_type, count in sorted(task_type_counts.items()):
            print(f"- {task_type}: {count}")

        print(f"\nTask instances per product:")
        for product in sorted(scheduler.delivery_dates.keys()):
            print(f"- {product}: {product_counts.get(product, 0)} instances")

        print(f"\nResources:")
        print(f"- Mechanic teams: {len(scheduler.team_capacity)}")
        print(f"- Quality teams: {len(scheduler.quality_team_capacity)}")

        # DIAGNOSTIC MODE (exits after diagnosis)
        if args.diagnose:
            print("\n" + "=" * 80)
            print("DIAGNOSTIC MODE")
            print("=" * 80)

            diagnostic_results = scheduler.run_diagnostic()

            print("\n" + "=" * 80)
            print("DIAGNOSTIC SUMMARY")
            print("=" * 80)
            print(f"Scheduling success rate: {diagnostic_results['scheduled']}/{diagnostic_results['total_tasks']} "
                  f"({100 * diagnostic_results['scheduled'] / diagnostic_results['total_tasks']:.1f}%)")

            if diagnostic_results['unscheduled']:
                print(f"\n⚠️ {len(diagnostic_results['unscheduled'])} tasks could not be scheduled!")
                print("See diagnostic report above for details.")

            sys.exit(0)

        # Storage for results
        results = {}

        # ========== SCENARIO 1: CSV-DEFINED HEADCOUNT ==========
        print("\n" + "=" * 80)
        print("SCENARIO 1: CSV-DEFINED HEADCOUNT")
        print("=" * 80)

        # Choose scheduling method based on arguments
        if args.critical_aware:
            print("Using critical-path-aware scheduling with safety buffers...")
            scheduler.schedule_tasks_with_critical_path_awareness(
                safety_buffer_days=2,
                silent_mode=False
            )
        elif args.level_loading:
            print("Using level-loading with 50% aggressiveness...")
            scheduler.schedule_tasks_with_level_loading(
                allow_late_delivery=True,
                aggressiveness=0.5,
                silent_mode=False
            )
        else:
            print("Using standard ASAP scheduling...")
            scheduler.schedule_tasks(
                allow_late_delivery=True,
                silent_mode=False
            )

        # Generate priority list and metrics
        scheduler.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)

        # Get lateness metrics for scenario 1
        scenario1_metrics = scheduler.calculate_lateness_metrics()

        results['scenario1'] = {
            'makespan': scheduler.calculate_makespan(),
            'metrics': scenario1_metrics,  # This is the product-level metrics
            'priority_list': scheduler.global_priority_list,
            'total_late_days': sum(
                max(0, m['lateness_days'])
                for m in scenario1_metrics.values()
                if m['lateness_days'] < 999999
            ),
            'avg_utilization': scheduler.calculate_average_utilization_properly(),
            'peak_utilization': scheduler.calculate_peak_utilization()
        }

        scheduler.export_results(filename='scheduling_results_scenario1.csv')
        max_lateness_s1 = scheduler.print_delivery_analysis("SCENARIO 1")

        if args.validate:
            print("\nValidating Scenario 1...")
            scheduler.validate_schedule_comprehensive(verbose=True)

        # ========== SCENARIO 2: MINIMIZE MAKESPAN ==========
        print("\n" + "=" * 80)
        print("SCENARIO 2: MINIMIZE MAKESPAN WITH UNIFORM CAPACITY")
        print("=" * 80)

        scenario2_result = scheduler.scenario_2_minimize_makespan(
            min_mechanics=1,
            max_mechanics=30,
            min_quality=1,
            max_quality=10
        )

        if scenario2_result:
            # Re-run with optimal configuration to get ACTUAL results
            scheduler.apply_capacity_configuration({
                'mechanic': {team: scenario2_result['optimal_mechanics']
                             for team in scheduler.team_capacity},
                'quality': {team: scenario2_result['optimal_quality']
                            for team in scheduler.quality_team_capacity}
            })
            scheduler.schedule_tasks(allow_late_delivery=True, silent_mode=True)
            scheduler.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)

            # Get the ACTUAL metrics after re-running
            scenario2_metrics = scheduler.calculate_lateness_metrics()

            # Update results with ACTUAL scheduling metrics
            results['scenario2'] = {
                'optimal_mechanics': scenario2_result['optimal_mechanics'],
                'optimal_quality': scenario2_result['optimal_quality'],
                'makespan': scheduler.calculate_makespan(),
                'metrics': scenario2_metrics,  # Use actual metrics from re-run
                'priority_list': scheduler.global_priority_list,
                'total_workforce': (scenario2_result['optimal_mechanics'] * len(scheduler.team_capacity) +
                                    scenario2_result['optimal_quality'] * len(scheduler.quality_team_capacity)),
                'avg_utilization': scheduler.calculate_average_utilization_properly(),
                'peak_utilization': scheduler.calculate_peak_utilization()
            }

            scheduler.export_results(filename='scheduling_results_scenario2.csv')
            max_lateness_s2 = scheduler.print_delivery_analysis("SCENARIO 2")

            if args.validate:
                print("\nValidating Scenario 2...")
                scheduler.validate_schedule_comprehensive(verbose=True)
        else:
            print("⚠️ Scenario 2 failed to find optimal uniform capacity")

        # ========== SCENARIO 3: OPTIMIZE FOR TARGET DELIVERY ==========
        print("\n" + "=" * 80)
        print("SCENARIO 3: OPTIMIZE FOR TARGET DELIVERY")
        print("=" * 80)

        # Store target for use in other methods
        scheduler.scenario_3_target = args.target_earliness

        # Use simulated annealing instead of smart optimization
        scenario3_result = scheduler.scenario_3_simulated_annealing(
            target_earliness=args.target_earliness,
            max_iterations=300,
            initial_temp=100,
            cooling_rate=0.95
        )

        if scenario3_result:
            # Re-apply optimal configuration for final results
            scheduler.apply_capacity_configuration(scenario3_result['config'])
            scheduler.task_schedule = {}
            scheduler._critical_path_cache = {}
            scheduler.schedule_tasks(allow_late_delivery=True, silent_mode=True)
            scheduler.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)

            # Get actual metrics from the new schedule
            actual_metrics = scheduler.calculate_lateness_metrics()

            results['scenario3'] = {
                'makespan': scheduler.calculate_makespan(),
                'metrics': actual_metrics,
                'total_workforce': scenario3_result['total_workforce'],
                'avg_utilization': scheduler.calculate_average_utilization_properly()
            }

            scheduler.export_results(filename='scheduling_results_scenario3.csv')
            max_lateness_s3 = scheduler.print_delivery_analysis("SCENARIO 3")

            if args.validate:
                print("\nValidating Scenario 3...")
                scheduler.validate_schedule_comprehensive(verbose=True)
        else:
            print("⚠️ Scenario 3 failed to find optimal configuration")


        # ========== FINAL COMPARISON ==========
        print("\n" + "=" * 80)
        print("SCENARIO COMPARISON")
        print("=" * 80)

        comparison_data = []

        for scenario_name, scenario_data in results.items():
            if scenario_data:
                # Calculate summary statistics based on scenario type
                lateness_values = []

                if 'metrics' in scenario_data:
                    # metrics should be the result of calculate_lateness_metrics()
                    # which returns {product: {lateness_days: X, ...}, ...}
                    metrics = scenario_data['metrics']

                    if isinstance(metrics, dict):
                        for product, product_metrics in metrics.items():
                            if isinstance(product_metrics, dict) and 'lateness_days' in product_metrics:
                                if product_metrics['lateness_days'] < 999999:
                                    lateness_values.append(product_metrics['lateness_days'])

                # Ensure we got all the values
                if lateness_values:
                    comparison_data.append({
                        'Scenario': scenario_name.replace('scenario', 'Scenario '),
                        'Makespan (days)': scenario_data.get('makespan', 'N/A'),
                        'Max Lateness': max(lateness_values),
                        'Avg Lateness': sum(lateness_values) / len(lateness_values),
                        'Products On Time': sum(1 for v in lateness_values if v <= 0),
                        'Avg Utilization': f"{scenario_data.get('avg_utilization', 0):.1f}%",
                        'Workforce': scenario_data.get('total_workforce',
                                                       scenario_data.get('total_headcount', 'CSV-defined'))
                    })

        # Print comparison table
        if comparison_data:
            # Header
            print(f"{'Metric':<20}", end='')
            for row in comparison_data:
                print(f" {row['Scenario']:<15}", end='')
            print()
            print("-" * (20 + 16 * len(comparison_data)))

            # Data rows
            metrics_to_show = ['Makespan (days)', 'Max Lateness', 'Avg Lateness',
                               'Products On Time', 'Avg Utilization', 'Workforce']

            for metric in metrics_to_show:
                print(f"{metric:<20}", end='')
                for row in comparison_data:
                    value = row[metric]
                    if isinstance(value, float):
                        print(f" {value:<15.1f}", end='')
                    else:
                        print(f" {str(value):<15}", end='')
                print()

        print("\n" + "=" * 80)
        print("ALL SCENARIOS COMPLETED SUCCESSFULLY")
        print("=" * 80)

        # Print best scenario recommendation
        if all(s in results for s in ['scenario1', 'scenario2', 'scenario3']):
            print("\nRecommendation:")
            print("- Scenario 1: Use when respecting current workforce constraints")
            print("- Scenario 2: Use when workforce can be standardized")
            print(f"- Scenario 3: Use when targeting {abs(args.target_earliness)} day early delivery")

    except FileNotFoundError:
        print(f"\n❌ ERROR: Could not find CSV file: {args.csv}")
        print("Please ensure the file exists and the path is correct.")
        sys.exit(1)

    except KeyError as e:
        print(f"\n❌ ERROR: Missing required column in CSV: {str(e)}")
        print("Please check your CSV file has all required columns.")
        sys.exit(1)

    except Exception as e:
        print("\n" + "!" * 80)
        print(f"❌ UNEXPECTED ERROR: {str(e)}")
        print("!" * 80)
        import traceback

        traceback.print_exc()
        sys.exit(1)