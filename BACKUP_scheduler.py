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

        # CRITICAL: Load team capacities FIRST, before any team mapping attempts
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

    def _load_team_capacities_and_schedules(self, sections):
        """Load team capacities and schedules FIRST"""

        # Load Mechanic Team Capacity
        if "MECHANIC TEAM CAPACITY" in sections:
            from io import StringIO
            df = pd.read_csv(StringIO(sections["MECHANIC TEAM CAPACITY"]))
            df.columns = df.columns.str.strip()
            for _, row in df.iterrows():
                team_name = row['Mechanic Team'].strip()
                capacity = int(row['Total Capacity (People)'])
                self.team_capacity[team_name] = capacity
                self._original_team_capacity[team_name] = capacity
            print(f"[DEBUG] Loaded capacity for {len(self.team_capacity)} mechanic teams")

        # Load Quality Team Capacity
        if "QUALITY TEAM CAPACITY" in sections:
            from io import StringIO
            df = pd.read_csv(StringIO(sections["QUALITY TEAM CAPACITY"]))
            df.columns = df.columns.str.strip()
            for _, row in df.iterrows():
                team_name = row['Quality Team'].strip()
                capacity = int(row['Total Capacity (People)'])
                self.quality_team_capacity[team_name] = capacity
                self._original_quality_capacity[team_name] = capacity
            print(f"[DEBUG] Loaded capacity for {len(self.quality_team_capacity)} quality teams")

        # Load Mechanic Team Working Calendars
        if "MECHANIC TEAM WORKING CALENDARS" in sections:
            from io import StringIO
            df = pd.read_csv(StringIO(sections["MECHANIC TEAM WORKING CALENDARS"]))
            df.columns = df.columns.str.strip()
            for _, row in df.iterrows():
                team_name = row['Mechanic Team'].strip()
                shifts = row['Working Shifts']

                if 'All 3 shifts' in shifts or 'all shifts' in shifts.lower():
                    self.team_shifts[team_name] = ['1st', '2nd', '3rd']
                elif 'and' in shifts:
                    shift_list = []
                    if '1st' in shifts:
                        shift_list.append('1st')
                    if '2nd' in shifts:
                        shift_list.append('2nd')
                    if '3rd' in shifts:
                        shift_list.append('3rd')
                    self.team_shifts[team_name] = shift_list
                elif ',' in shifts:
                    self.team_shifts[team_name] = [s.strip() for s in shifts.split(',')]
                else:
                    self.team_shifts[team_name] = [shifts.strip()]
            print(f"[DEBUG] Loaded {len(self.team_shifts)} mechanic team schedules")

        # Load Quality Team Working Calendars
        if "QUALITY TEAM WORKING CALENDARS" in sections:
            from io import StringIO
            df = pd.read_csv(StringIO(sections["QUALITY TEAM WORKING CALENDARS"]))
            df.columns = df.columns.str.strip()
            for _, row in df.iterrows():
                team_name = row['Quality Team'].strip()
                shifts = row['Working Shifts']

                if 'All 3 shifts' in shifts or 'all shifts' in shifts.lower():
                    self.quality_team_shifts[team_name] = ['1st', '2nd', '3rd']
                elif 'and' in shifts:
                    shift_list = []
                    if '1st' in shifts:
                        shift_list.append('1st')
                    if '2nd' in shifts:
                        shift_list.append('2nd')
                    if '3rd' in shifts:
                        shift_list.append('3rd')
                    self.quality_team_shifts[team_name] = shift_list
                elif ',' in shifts:
                    self.quality_team_shifts[team_name] = [s.strip() for s in shifts.split(',')]
                else:
                    self.quality_team_shifts[team_name] = [shifts.strip()]
            print(f"[DEBUG] Loaded {len(self.quality_team_shifts)} quality team schedules")

        # Load Shift Working Hours
        if "SHIFT WORKING HOURS" in sections:
            from io import StringIO
            df = pd.read_csv(StringIO(sections["SHIFT WORKING HOURS"]))
            df.columns = df.columns.str.strip()
            for _, row in df.iterrows():
                shift_name = row['Shift'].strip()
                self.shift_hours[shift_name] = {
                    'start': row['Start Time'].strip(),
                    'end': row['End Time'].strip()
                }
            print(f"[DEBUG] Loaded {len(self.shift_hours)} shift definitions")

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
            task_count = 0
            for _, row in df.iterrows():
                try:
                    task_id = int(row['Task'])
                    if pd.isna(row.get('Duration (minutes)')) or pd.isna(row.get('Resource Type')) or pd.isna(
                            row.get('Mechanics Required')):
                        print(f"[WARNING] Skipping incomplete task row: {row}")
                        continue

                    self.baseline_task_data[task_id] = {
                        'duration': int(row['Duration (minutes)']),
                        'team': row['Resource Type'].strip(),
                        'mechanics_required': int(row['Mechanics Required']),
                        'is_quality': False,
                        'task_type': 'Production'
                    }
                    task_count += 1
                except (ValueError, KeyError) as e:
                    print(f"[WARNING] Error processing task row: {row}, Error: {e}")
                    continue
            print(f"[DEBUG] Loaded {task_count} baseline task definitions")

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
                        task_data = self.baseline_task_data[task_id].copy()
                        task_data['product'] = product
                        task_data['original_task_id'] = task_id

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
        """Load late parts and rework tasks with 1:1 team mapping for QI"""

        # Load Late Parts Relationships
        if "LATE PARTS RELATIONSHIPS TABLE" in sections:
            from io import StringIO
            df = pd.read_csv(StringIO(sections["LATE PARTS RELATIONSHIPS TABLE"]))
            df.columns = df.columns.str.strip()
            lp_count = 0
            has_product_column = 'Product Line' in df.columns

            for _, row in df.iterrows():
                try:
                    first_task = int(row['First'])
                    second_task = int(row['Second'])
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

        # Load Late Parts Task Details
        if "LATE PARTS TASK DETAILS" in sections:
            from io import StringIO
            df = pd.read_csv(StringIO(sections["LATE PARTS TASK DETAILS"]))
            df.columns = df.columns.str.strip()
            lp_task_count = 0

            for _, row in df.iterrows():
                try:
                    task_id = int(row['Task'])
                    if pd.isna(row.get('Duration (minutes)')) or pd.isna(row.get('Resource Type')) or pd.isna(
                            row.get('Mechanics Required')):
                        print(f"[WARNING] Skipping incomplete late part task row: {row}")
                        continue

                    product = None
                    for constraint in self.late_part_constraints:
                        if constraint['First'] == task_id and constraint.get('Product_Line'):
                            product = constraint['Product_Line']
                            break

                    instance_id = f"LP_{task_id}"

                    self.tasks[instance_id] = {
                        'duration': int(row['Duration (minutes)']),
                        'team': row['Resource Type'].strip(),
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
            print(f"[DEBUG] Created {lp_task_count} late part task instances")

        # Load Rework Relationships
        if "REWORK RELATIONSHIPS TABLE" in sections:
            from io import StringIO
            df = pd.read_csv(StringIO(sections["REWORK RELATIONSHIPS TABLE"]))
            df.columns = df.columns.str.strip()
            rw_count = 0
            has_product_column = 'Product Line' in df.columns

            for _, row in df.iterrows():
                try:
                    first_task = int(row['First'])
                    second_task = int(row['Second'])
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

        # Load Rework Task Details with 1:1 QI team mapping
        if "REWORK TASK DETAILS" in sections:
            from io import StringIO
            df = pd.read_csv(StringIO(sections["REWORK TASK DETAILS"]))
            df.columns = df.columns.str.strip()
            rw_task_count = 0
            rw_qi_count = 0

            for _, row in df.iterrows():
                try:
                    task_id = int(row['Task'])
                    if pd.isna(row.get('Duration (minutes)')) or pd.isna(row.get('Resource Type')) or pd.isna(
                            row.get('Mechanics Required')):
                        print(f"[WARNING] Skipping incomplete rework task row: {row}")
                        continue

                    product = None
                    for constraint in self.rework_constraints:
                        if constraint['First'] == task_id and constraint.get('Product_Line'):
                            product = constraint['Product_Line']
                            break
                        elif constraint['Second'] == task_id and constraint.get('Product_Line'):
                            product = constraint['Product_Line']
                            break

                    instance_id = f"RW_{task_id}"
                    rework_team = row['Resource Type'].strip()

                    self.tasks[instance_id] = {
                        'duration': int(row['Duration (minutes)']),
                        'team': rework_team,
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
                        # Create quality inspection for rework task with 1:1 team mapping
                        qi_instance_id = f"RW_QI_{task_id}"

                        # Get the quality team based on the rework task's team
                        quality_team = self.map_mechanic_to_quality_team(rework_team)

                        self.quality_requirements[instance_id] = qi_instance_id

                        self.tasks[qi_instance_id] = {
                            'duration': qi_duration,
                            'team': quality_team,  # ASSIGNED BASED ON REWORK TEAM!
                            'mechanics_required': qi_headcount,
                            'is_quality': True,
                            'task_type': 'Quality Inspection',
                            'primary_task': instance_id,
                            'product': product,
                            'original_task_id': task_id + 10000
                        }

                        self.quality_inspections[qi_instance_id] = {
                            'primary_task': instance_id,
                            'headcount': qi_headcount
                        }

                        if product:
                            self.instance_to_product[qi_instance_id] = product
                        self.instance_to_original_task[qi_instance_id] = task_id + 10000

                        rw_qi_count += 1

                    rw_task_count += 1
                except (ValueError, KeyError) as e:
                    print(f"[WARNING] Error processing rework task row: {row}, Error: {e}")
                    continue

            print(f"[DEBUG] Created {rw_task_count} rework task instances")
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
            print(f"    {team}: {capacity} people, shifts: {', '.join(shifts)}")

        print(f"  - Quality teams: {len(self.quality_team_capacity)}")
        total_quality = sum(self.quality_team_capacity.values())
        print(f"    Total quality capacity: {total_quality}")
        for team, capacity in sorted(self.quality_team_capacity.items()):
            shifts = self.quality_team_shifts.get(team, [])
            print(f"    {team}: {capacity} people, shifts: {', '.join(shifts)}")

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
        Build dependency graph with support for ALL relationship types
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
            first_task_id = lp_constraint['First']
            second_task_id = lp_constraint['Second']
            product = lp_constraint.get('Product_Line')
            relationship = self._normalize_relationship_type(lp_constraint.get('Relationship', 'Finish <= Start'))

            first_instance = f"LP_{first_task_id}"

            if second_task_id >= 1000:
                second_instance = f"LP_{second_task_id}" if second_task_id in range(1000,
                                                                                    2000) else f"RW_{second_task_id}"
            else:
                second_instance = self.task_instance_map.get((product, second_task_id)) if product else None

            if first_instance in self.tasks and second_instance and second_instance in self.tasks:
                dynamic_constraints.append({
                    'First': first_instance,
                    'Second': second_instance,
                    'Relationship': relationship,
                    'Type': 'Late Part',
                    'Product': product
                })

        # 3. Add rework constraints
        for rw_constraint in self.rework_constraints:
            first_task_id = rw_constraint['First']
            second_task_id = rw_constraint['Second']
            relationship = self._normalize_relationship_type(rw_constraint.get('Relationship', 'Finish <= Start'))
            product = rw_constraint.get('Product_Line')

            first_instance = f"RW_{first_task_id}"

            if second_task_id >= 1000:
                second_instance = f"RW_{second_task_id}" if second_task_id >= 2000 else f"LP_{second_task_id}"
            else:
                second_instance = self.task_instance_map.get((product, second_task_id)) if product else None

            if first_instance in self.tasks and second_instance and second_instance in self.tasks:
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

        # 4. Add remaining QI constraints
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

        rel_counts = defaultdict(int)
        for c in dynamic_constraints:
            rel_counts[c['Relationship']] += 1

        if self.debug:
            for rel_type, count in sorted(rel_counts.items()):
                self.debug_print(f"  {rel_type}: {count}")

        self._dynamic_constraints_cache = dynamic_constraints
        return dynamic_constraints

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
        """Schedule all task instances with fixed quality team assignments"""
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

        # CRITICAL FIX: Find ALL tasks that can be scheduled initially
        # This includes tasks with no constraints AND tasks with no blocking constraints
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

        # Also add tasks that have only outgoing constraints (no incoming)
        tasks_with_only_outgoing = tasks_with_outgoing_constraints - tasks_with_incoming_constraints
        for task in tasks_with_only_outgoing:
            priority = self.calculate_task_priority(task)
            heapq.heappush(ready_tasks, (priority, task))

        # Finally, add tasks with incoming constraints that aren't blocking
        for task in tasks_with_incoming_constraints:
            constraints = constraints_by_second.get(task, [])

            has_blocking_constraints = False
            for c in constraints:
                rel = c['Relationship']
                # These relationships block the task from starting immediately
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
                    team = task_info.get('team')

                    if not team:
                        print(f"[ERROR] Quality task {task_instance_id} has no team assigned!")
                        # Try to recover
                        if task_instance_id in self.quality_inspections:
                            primary_task_id = self.quality_inspections[task_instance_id].get('primary_task')
                            if primary_task_id and primary_task_id in self.tasks:
                                primary_team = self.tasks[primary_task_id].get('team')
                                team = self.map_mechanic_to_quality_team(primary_team)
                                if team:
                                    task_info['team'] = team
                                    print(f"[RECOVERY] Assigned {team} to {task_instance_id}")

                        if not team:
                            task_retry_counts[task_instance_id] += 1
                            if task_retry_counts[task_instance_id] < 3:
                                heapq.heappush(ready_tasks, (priority + 0.1, task_instance_id))
                            continue

                    scheduled_start, shift = self.get_next_working_time_with_capacity(
                        earliest_start, product, team,
                        mechanics_needed, duration, is_quality=True)
                else:
                    team = task_info['team']
                    scheduled_start, shift = self.get_next_working_time_with_capacity(
                        earliest_start, product, team,
                        mechanics_needed, duration, is_quality=False)

                scheduled_end = scheduled_start + timedelta(minutes=int(duration))

                self.task_schedule[task_instance_id] = {
                    'start_time': scheduled_start,
                    'end_time': scheduled_end,
                    'team': team,
                    'product': product,
                    'duration': duration,
                    'mechanics_required': mechanics_needed,
                    'is_quality': is_quality,
                    'task_type': task_type,
                    'shift': shift,
                    'original_task_id': self.instance_to_original_task.get(task_instance_id)
                }

                scheduled_count += 1

                # Add dependent tasks to ready queue
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

                # List some unscheduled tasks for debugging
                unscheduled_list = [t for t in all_tasks if t not in self.task_schedule][:10]
                print(f"[DEBUG] First 10 unscheduled tasks: {unscheduled_list}")

        self.debug = original_debug

    def calculate_task_priority(self, task_instance_id):
        """Calculate priority for a task instance"""
        task_info = self.tasks[task_instance_id]

        if task_instance_id in self.late_part_tasks:
            return -3000

        if task_instance_id in self.quality_inspections:
            return -2000

        if task_instance_id in self.rework_tasks:
            return -1000

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
        original_task_id = self.instance_to_original_task.get(task_instance_id)
        if original_task_id not in self.on_dock_dates:
            return datetime(2025, 8, 22, 6, 0)

        on_dock_date = self.on_dock_dates[original_task_id]
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

    def is_working_day(self, date, product_line):
        """Check if a date is a working day for a specific product line"""
        if date.weekday() >= 5:
            return False
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

    def get_next_working_time_with_capacity(self, current_time, product_line, team, mechanics_needed,
                                            duration, is_quality=False):
        """Get next available working time when team has capacity"""
        max_iterations = 5000
        iterations = 0

        while iterations < max_iterations:
            iterations += 1

            if not self.is_working_day(current_time, product_line):
                current_time = current_time.replace(hour=6, minute=0, second=0)
                current_time += timedelta(days=1)
                continue

            current_minutes = current_time.hour * 60 + current_time.minute
            available_shift = None

            if is_quality:
                team_shifts = self.quality_team_shifts.get(team, [])
            else:
                team_shifts = self.team_shifts.get(team, [])

            for shift in team_shifts:
                if shift == '1st' and 360 <= current_minutes < 870:
                    available_shift = shift
                    break
                elif shift == '2nd' and 870 <= current_minutes < 1380:
                    available_shift = shift
                    break
                elif shift == '3rd' and (current_minutes >= 1380 or current_minutes < 360):
                    available_shift = shift
                    break

            if available_shift:
                end_time = current_time + timedelta(minutes=duration)
                if self.check_team_capacity_at_time(team, current_time, end_time, mechanics_needed):
                    return current_time, available_shift
                else:
                    current_time += timedelta(minutes=1)
            else:
                if current_minutes < 360:
                    current_time = current_time.replace(hour=6, minute=0, second=0)
                elif current_minutes < 870:
                    current_time = current_time.replace(hour=14, minute=30, second=0)
                elif current_minutes < 1380:
                    current_time = current_time.replace(hour=23, minute=0, second=0)
                else:
                    current_time = current_time.replace(hour=6, minute=0, second=0)
                    current_time += timedelta(days=1)

        raise RuntimeError(f"Could not find working time with capacity after {max_iterations} iterations!")

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

            priority_data.append({
                'task_instance_id': task_instance_id,
                'task_type': task_type,
                'display_name': display_name,
                'product_line': product,
                'original_task_id': original_task_id,
                'team': schedule['team'],
                'scheduled_start': schedule['start_time'],
                'scheduled_end': schedule['end_time'],
                'duration_minutes': schedule['duration'],
                'mechanics_required': schedule['mechanics_required'],
                'slack_hours': slack,
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

    def calculate_slack_time(self, task_instance_id):
        """Calculate slack time for a task instance"""
        task_info = self.tasks.get(task_instance_id, {})
        product = task_info.get('product')

        if not product or product not in self.delivery_dates:
            return float('inf')

        delivery_date = self.delivery_dates[product]

        dynamic_constraints = self.build_dynamic_dependencies()
        all_successors = set()
        stack = [task_instance_id]

        while stack:
            current = stack.pop()
            for constraint in dynamic_constraints:
                if constraint['First'] == current:
                    successor = constraint['Second']
                    if successor not in all_successors:
                        all_successors.add(successor)
                        stack.append(successor)

        total_successor_duration = sum(int(self.tasks[succ]['duration'])
                                       for succ in all_successors if succ in self.tasks)

        buffer_days = total_successor_duration / (8 * 60)
        latest_start = delivery_date - timedelta(days=buffer_days + 2)

        if task_instance_id in self.task_schedule:
            scheduled_start = self.task_schedule[task_instance_id]['start_time']
            slack = (latest_start - scheduled_start).total_seconds() / 3600
            return slack
        else:
            return 0

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

    def calculate_minimum_team_requirements(self):
        """Calculate the minimum required capacity for each team based on task requirements"""
        min_requirements = {}

        for team in self.team_capacity:
            min_requirements[team] = 0
        for team in self.quality_team_capacity:
            min_requirements[team] = 0

        for task_id, task_info in self.tasks.items():
            team = task_info.get('team')
            mechanics_required = task_info.get('mechanics_required', 0)

            if team:
                if team in min_requirements:
                    min_requirements[team] = max(min_requirements[team], mechanics_required)
                else:
                    min_requirements[team] = mechanics_required

        for qi_id, qi_info in self.quality_inspections.items():
            headcount = qi_info.get('headcount', 0)
            for team in self.quality_team_capacity:
                min_requirements[team] = max(min_requirements.get(team, 0), headcount)

        return min_requirements

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

    def scenario_2_minimize_makespan(self, min_mechanics=1, max_mechanics=50, min_quality=1, max_quality=20):
        """Scenario 2: Find optimal MINIMUM uniform headcount for minimum makespan"""
        print("\n" + "=" * 80)
        print("SCENARIO 2: Minimize Makespan with Uniform Headcount")
        print("=" * 80)

        min_requirements = self.calculate_minimum_team_requirements()

        min_mech_required = max([min_requirements.get(team, 1) for team in self.team_capacity], default=1)
        min_qual_required = max([min_requirements.get(team, 1) for team in self.quality_team_capacity], default=1)

        min_mechanics = max(min_mechanics, min_mech_required)
        min_quality = max(min_quality, min_qual_required)

        print(f"\nAdjusted search bounds based on task requirements:")
        print(f"  Mechanics: {min_mechanics} to {max_mechanics}")
        print(f"  Quality: {min_quality} to {max_quality}")

        best_makespan = float('inf')
        best_mech = max_mechanics
        best_qual = max_quality

        print("\nPhase 1: Finding optimal mechanic headcount...")
        mech_low, mech_high = min_mechanics, max_mechanics

        while mech_low <= mech_high:
            mech_mid = (mech_low + mech_high) // 2

            for team in self.team_capacity:
                self.team_capacity[team] = mech_mid
            for team in self.quality_team_capacity:
                self.quality_team_capacity[team] = max_quality

            self._critical_path_cache = {}

            try:
                self.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)
                scheduled_count = len(self.task_schedule)
                total_tasks = len(self.tasks)

                if scheduled_count < total_tasks:
                    print(f"  Mechanics: {mech_mid} -> Failed ({scheduled_count}/{total_tasks})")
                    mech_low = mech_mid + 1
                    continue

                makespan = self.calculate_makespan()
                print(f"  Mechanics: {mech_mid} -> Makespan: {makespan} days")

                if makespan < best_makespan:
                    best_makespan = makespan
                    best_mech = mech_mid
                    mech_high = mech_mid - 1
                elif makespan == best_makespan:
                    if mech_mid < best_mech:
                        best_mech = mech_mid
                    mech_high = mech_mid - 1
                else:
                    mech_low = mech_mid + 1

            except Exception as e:
                print(f"  Mechanics: {mech_mid} -> Failed: {str(e)}")
                mech_low = mech_mid + 1

        print(f"\nOptimal mechanics: {best_mech} (achieves makespan of {best_makespan} days)")
        print("Phase 2: Finding optimal quality headcount...")

        for team in self.team_capacity:
            self.team_capacity[team] = best_mech

        qual_low, qual_high = min_quality, max_quality

        while qual_low <= qual_high:
            qual_mid = (qual_low + qual_high) // 2

            for team in self.quality_team_capacity:
                self.quality_team_capacity[team] = qual_mid

            self._critical_path_cache = {}

            try:
                self.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)
                scheduled_count = len(self.task_schedule)
                total_tasks = len(self.tasks)

                if scheduled_count < total_tasks:
                    print(f"  Quality: {qual_mid} -> Failed ({scheduled_count}/{total_tasks})")
                    qual_low = qual_mid + 1
                    continue

                makespan = self.calculate_makespan()
                print(f"  Quality: {qual_mid} -> Makespan: {makespan} days")

                if makespan <= best_makespan:
                    best_qual = qual_mid
                    qual_high = qual_mid - 1
                else:
                    qual_low = qual_mid + 1

            except Exception as e:
                print(f"  Quality: {qual_mid} -> Failed: {str(e)}")
                qual_low = qual_mid + 1

        print("\nGenerating optimal schedule...")
        for team in self.team_capacity:
            self.team_capacity[team] = best_mech
        for team in self.quality_team_capacity:
            self.quality_team_capacity[team] = best_qual

        self.task_schedule = {}
        self._critical_path_cache = {}
        priority_list = self.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)

        makespan = self.calculate_makespan()
        metrics = self.calculate_lateness_metrics()

        print("\n" + "=" * 80)
        print("OPTIMAL CONFIGURATION")
        print("=" * 80)
        print(f"Mechanics per team: {best_mech}")
        print(f"Quality inspectors per team: {best_qual}")
        print(f"Minimum makespan: {best_makespan} working days")

        total_mechanics = best_mech * len(self.team_capacity)
        total_quality = best_qual * len(self.quality_team_capacity)
        total_headcount = total_mechanics + total_quality

        print(f"\nTotal workforce required: {total_headcount}")
        print(f"  - Total mechanics: {total_mechanics} ({len(self.team_capacity)} teams × {best_mech})")
        print(f"  - Total quality: {total_quality} ({len(self.quality_team_capacity)} teams × {best_qual})")

        for team, capacity in self._original_team_capacity.items():
            self.team_capacity[team] = capacity
        for team, capacity in self._original_quality_capacity.items():
            self.quality_team_capacity[team] = capacity

        return {
            'optimal_mechanics': best_mech,
            'optimal_quality': best_qual,
            'makespan': makespan,
            'metrics': metrics,
            'total_headcount': total_headcount,
            'priority_list': priority_list
        }

    def scenario_3_multidimensional_optimization(self, target_lateness=-1, scenario2_results=None, max_iterations=300):
        """
        Scenario 3: Find MINIMUM workforce to achieve TARGET lateness
        """
        print("\n" + "=" * 80)
        print("SCENARIO 3: Targeted Multi-Dimensional Optimization")
        print(f"Target: {abs(target_lateness)} day{'s' if abs(target_lateness) != 1 else ''} "
              f"{'early' if target_lateness < 0 else 'late' if target_lateness > 0 else 'on-time'}")
        print("=" * 80)

        original_team = self._original_team_capacity.copy()
        original_quality = self._original_quality_capacity.copy()

        min_requirements = self.calculate_minimum_team_requirements()

        print("\nMinimum team requirements (based on task needs):")
        for team, min_req in sorted(min_requirements.items()):
            if 'Quality' not in team:
                print(f"  {team}: {min_req} mechanics minimum")
        for team, min_req in sorted(min_requirements.items()):
            if 'Quality' in team:
                print(f"  {team}: {min_req} inspectors minimum")

        if scenario2_results:
            max_mechanics = scenario2_results.get('optimal_mechanics', 30)
            max_quality = scenario2_results.get('optimal_quality', 15)
            print(f"\nUsing Scenario 2 results as upper bound:")
            print(f"  Max mechanics per team: {max_mechanics}")
            print(f"  Max quality per team: {max_quality}")
        else:
            max_mechanics = 30
            max_quality = 15

        current_mech_config = {}
        current_qual_config = {}

        for team in original_team:
            current_mech_config[team] = max(min_requirements.get(team, 1), 1)
        for team in original_quality:
            current_qual_config[team] = max(min_requirements.get(team, 1), 1)

        best_config = None
        best_total_workforce = float('inf')
        best_metrics = None
        best_max_lateness = float('inf')

        iteration = 0
        achieved_target = False
        consecutive_no_improvement = 0

        print("\n" + "=" * 80)
        print(f"PHASE 1: Achieving Target Lateness ({target_lateness} days)")
        print("-" * 80)

        while iteration < max_iterations // 2 and not achieved_target:
            iteration += 1

            for team, capacity in current_mech_config.items():
                self.team_capacity[team] = capacity
            for team, capacity in current_qual_config.items():
                self.quality_team_capacity[team] = capacity

            self.task_schedule = {}
            self._critical_path_cache = {}

            try:
                self.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)
                scheduled_count = len(self.task_schedule)
                total_tasks = len(self.tasks)

                if scheduled_count < total_tasks:
                    if iteration % 5 == 0:
                        print(f"  Iteration {iteration}: Incomplete ({scheduled_count}/{total_tasks})")

                    failure_rate = (total_tasks - scheduled_count) / total_tasks
                    if failure_rate > 0.5:
                        for team in list(current_mech_config.keys())[:2]:
                            if current_mech_config[team] < max_mechanics:
                                current_mech_config[team] += 2
                    else:
                        for team in current_mech_config:
                            if current_mech_config[team] < max_mechanics:
                                current_mech_config[team] += 1
                                break
                    continue

                metrics = self.calculate_lateness_metrics()
                max_lateness = max((data['lateness_days'] for data in metrics.values()
                                    if data['lateness_days'] < 999999), default=999)
                total_workforce = sum(current_mech_config.values()) + sum(current_qual_config.values())
                late_products = sum(1 for data in metrics.values()
                                    if data['lateness_days'] > 0 and data['lateness_days'] < 999999)

                if max_lateness <= target_lateness:
                    print(f"  Iteration {iteration}: ✓ ACHIEVED TARGET!")
                    print(f"    Lateness = {max_lateness} days ≤ {target_lateness} days")
                    print(f"    Workforce = {total_workforce}")

                    best_config = {
                        'mechanic': current_mech_config.copy(),
                        'quality': current_qual_config.copy()
                    }
                    best_total_workforce = total_workforce
                    best_max_lateness = max_lateness
                    best_metrics = metrics
                    achieved_target = True
                    break
                else:
                    if iteration % 5 == 0 or iteration == 1:
                        print(f"  Iteration {iteration}: Lateness = {max_lateness} days, "
                              f"Workforce = {total_workforce}")

                    gap = max_lateness - target_lateness
                    if gap > 10:
                        increment = 2
                    else:
                        increment = 1

                    team_increased = False
                    for team in current_mech_config:
                        if current_mech_config[team] < max_mechanics:
                            current_mech_config[team] += increment
                            team_increased = True
                            break

                    if not team_increased:
                        for team in current_qual_config:
                            if current_qual_config[team] < max_quality:
                                current_qual_config[team] += 1
                                break

            except Exception as e:
                print(f"  Error at iteration {iteration}: {str(e)}")
                for team in current_mech_config:
                    if current_mech_config[team] < max_mechanics:
                        current_mech_config[team] += 1
                        break

        if achieved_target and best_config:
            print("\n" + "=" * 80)
            print("PHASE 2: Optimizing Workforce While Maintaining Target")
            print("-" * 80)

            test_config_mech = best_config['mechanic'].copy()
            test_config_qual = best_config['quality'].copy()

            reduction_attempts = 0
            successful_reductions = 0

            while iteration < max_iterations and reduction_attempts < 50:
                iteration += 1
                reduction_attempts += 1

                team_to_reduce = None

                for team in test_config_mech:
                    if test_config_mech[team] > min_requirements.get(team, 1):
                        team_to_reduce = team
                        break

                if not team_to_reduce:
                    for team in test_config_qual:
                        if test_config_qual[team] > min_requirements.get(team, 1):
                            team_to_reduce = ('quality', team)
                            break
                    else:
                        print("  All teams at minimum capacity")
                        break

                if isinstance(team_to_reduce, tuple):
                    test_config_qual[team_to_reduce[1]] -= 1
                    team_name = team_to_reduce[1]
                else:
                    test_config_mech[team_to_reduce] -= 1
                    team_name = team_to_reduce

                for team, capacity in test_config_mech.items():
                    self.team_capacity[team] = capacity
                for team, capacity in test_config_qual.items():
                    self.quality_team_capacity[team] = capacity

                self.task_schedule = {}
                self._critical_path_cache = {}

                try:
                    self.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)

                    if len(self.task_schedule) == len(self.tasks):
                        metrics = self.calculate_lateness_metrics()
                        max_lateness = max((data['lateness_days'] for data in metrics.values()
                                            if data['lateness_days'] < 999999), default=999)
                        total_workforce = sum(test_config_mech.values()) + sum(test_config_qual.values())

                        if max_lateness <= target_lateness:
                            successful_reductions += 1
                            print(f"  ✓ Reduced {team_name} by 1, workforce now {total_workforce}")
                            best_config = {
                                'mechanic': test_config_mech.copy(),
                                'quality': test_config_qual.copy()
                            }
                            best_total_workforce = total_workforce
                            best_max_lateness = max_lateness
                            best_metrics = metrics
                        else:
                            if isinstance(team_to_reduce, tuple):
                                test_config_qual[team_to_reduce[1]] += 1
                            else:
                                test_config_mech[team_to_reduce] += 1
                    else:
                        if isinstance(team_to_reduce, tuple):
                            test_config_qual[team_to_reduce[1]] += 1
                        else:
                            test_config_mech[team_to_reduce] += 1

                except:
                    if isinstance(team_to_reduce, tuple):
                        test_config_qual[team_to_reduce[1]] += 1
                    else:
                        test_config_mech[team_to_reduce] += 1

            if successful_reductions > 0:
                print(f"  Successfully reduced workforce by {successful_reductions} people")

        if best_config:
            for team, capacity in best_config['mechanic'].items():
                self.team_capacity[team] = capacity
            for team, capacity in best_config['quality'].items():
                self.quality_team_capacity[team] = capacity

            self.task_schedule = {}
            self._critical_path_cache = {}
            priority_list = self.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)

            makespan = self.calculate_makespan()
            metrics = self.calculate_lateness_metrics()

            print("\n" + "=" * 80)
            print("OPTIMIZATION RESULTS")
            print("=" * 80)
            print(f"\nTarget Achievement:")
            print(f"  Target: {target_lateness} days")
            print(f"  Achieved: {best_max_lateness} days")
            print(f"  {'✓ SUCCESS' if best_max_lateness <= target_lateness else '✗ FAILED'}")

            print(f"\nOptimal Configuration:")
            print(f"  Total Workforce: {best_total_workforce}")
            print(f"  Makespan: {makespan} working days")

            print("\nMechanic Teams:")
            for team in sorted(best_config['mechanic'].keys()):
                capacity = best_config['mechanic'][team]
                min_req = min_requirements.get(team, 1)
                print(f"  {team}: {capacity} people (min required: {min_req})")

            print("\nQuality Teams:")
            for team in sorted(best_config['quality'].keys()):
                capacity = best_config['quality'][team]
                min_req = min_requirements.get(team, 1)
                print(f"  {team}: {capacity} people (min required: {min_req})")

            print("\nProduct Delivery Status:")
            on_time = 0
            late = 0
            for product, data in sorted(metrics.items()):
                if data['lateness_days'] <= 0:
                    on_time += 1
                elif data['lateness_days'] < 999999:
                    late += 1
                    print(f"  {product}: {data['lateness_days']} days late")

            print(f"\nSummary: {on_time} on-time, {late} late")

            if scenario2_results:
                s2_workforce = scenario2_results['total_headcount']
                savings = s2_workforce - best_total_workforce
                if savings > 0:
                    print(f"\n✓ Workforce savings vs Scenario 2: {savings} people "
                          f"({100 * savings / s2_workforce:.1f}% reduction)")

            for team, capacity in original_team.items():
                self.team_capacity[team] = capacity
            for team, capacity in original_quality.items():
                self.quality_team_capacity[team] = capacity

            return {
                'config': best_config,
                'total_workforce': best_total_workforce,
                'makespan': makespan,
                'metrics': metrics,
                'max_lateness': best_max_lateness,
                'target_lateness': target_lateness,
                'target_achieved': best_max_lateness <= target_lateness,
                'priority_list': priority_list,
                'min_requirements': min_requirements
            }
        else:
            print("\n[ERROR] Could not achieve target lateness!")

            for team, capacity in original_team.items():
                self.team_capacity[team] = capacity
            for team, capacity in original_quality.items():
                self.quality_team_capacity[team] = capacity

            return None

    #####################

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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Production Scheduler')
    parser.add_argument('--csv', type=str, default='scheduling_data.csv',
                        help='Path to CSV file')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug output')
    parser.add_argument('--target-lateness', type=int, default=-1,
                        help='Target lateness for Scenario 3')
    parser.add_argument('--validate', action='store_true',
                        help='Run validation after each scenario')
    parser.add_argument('--diagnose', action='store_true',
                        help='Run diagnostic to identify scheduling issues')

    args = parser.parse_args()

    try:
        scheduler = ProductionScheduler(args.csv, debug=args.debug)

        print("Loading data from CSV...")
        scheduler.load_data_from_csv()

        print("\n" + "=" * 80)
        print("DATA LOADED SUCCESSFULLY")
        print("=" * 80)

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

        print(f"\nProduct lines: {len(scheduler.delivery_dates)}")
        print(f"Mechanic teams: {len(scheduler.team_capacity)}")
        print(f"Quality teams: {len(scheduler.quality_team_capacity)}")

        # Run diagnostic if requested
        if args.diagnose:
            print("\n" + "=" * 80)
            print("Running Diagnostic Mode...")
            print("=" * 80)

            diagnostic_results = scheduler.run_diagnostic()

            # Print summary
            print("\n" + "=" * 80)
            print("DIAGNOSTIC SUMMARY")
            print("=" * 80)
            print(f"Scheduling success rate: {diagnostic_results['scheduled']}/{diagnostic_results['total_tasks']} "
                  f"({100 * diagnostic_results['scheduled'] / diagnostic_results['total_tasks']:.1f}%)")

            if diagnostic_results['unscheduled']:
                print(f"\n⚠️  {len(diagnostic_results['unscheduled'])} tasks could not be scheduled!")
                print("See diagnostic report above for details.")

            # Exit after diagnostic
            sys.exit(0)

        results = {}

        print("\n" + "=" * 80)
        print("Running Scenario 1...")
        print("=" * 80)
        results['scenario1'] = scheduler.scenario_1_csv_headcount()
        scheduler.export_results(scenario_name='scenario1')

        if args.validate:
            print("\nValidating Scenario 1...")
            scheduler.validate_schedule_comprehensive(verbose=True)

        print("\n" + "=" * 80)
        print("Running Scenario 2...")
        print("=" * 80)
        results['scenario2'] = scheduler.scenario_2_minimize_makespan(
            min_mechanics=1, max_mechanics=30,
            min_quality=1, max_quality=10
        )
        scheduler.export_results(scenario_name='scenario2')

        if args.validate:
            print("\nValidating Scenario 2...")
            scheduler.validate_schedule_comprehensive(verbose=True)

        print("\n" + "=" * 80)
        print("Running Scenario 3...")
        print("=" * 80)
        results['scenario3'] = scheduler.scenario_3_multidimensional_optimization(
            target_lateness=args.target_lateness,
            scenario2_results=results.get('scenario2'),
            max_iterations=300
        )
        if results['scenario3']:
            scheduler.export_results(scenario_name='scenario3')

            if args.validate:
                print("\nValidating Scenario 3...")
                scheduler.validate_schedule_comprehensive(verbose=True)

        print("\n" + "=" * 80)
        print("ALL SCENARIOS COMPLETED")
        print("=" * 80)

        print("\nSummary:")
        print(f"Scenario 1 - Makespan: {results['scenario1']['makespan']} days, "
              f"Total late: {results['scenario1']['total_late_days']} days")
        print(f"Scenario 2 - Makespan: {results['scenario2']['makespan']} days, "
              f"Workforce: {results['scenario2']['total_headcount']}")
        if results['scenario3']:
            print(f"Scenario 3 - Target: {results['scenario3']['target_lateness']} days, "
                  f"Achieved: {results['scenario3']['max_lateness']} days, "
                  f"Workforce: {results['scenario3']['total_workforce']}")

    except Exception as e:
        print("\n" + "!" * 80)
        print(f"ERROR: {str(e)}")
        print("!" * 80)
        import traceback

        traceback.print_exc()
        sys.exit(1)
