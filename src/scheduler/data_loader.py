# src/scheduler/data_loader.py

import pandas as pd
import csv
import re
from collections import defaultdict
from io import StringIO

def parse_csv_sections(scheduler, file_content):
    """Parse CSV file content into separate sections based on ==== markers"""
    sections = {}
    current_section = None
    current_data = []

    for line in file_content.strip().split('\n'):
        if '====' in line and line.strip().startswith('===='):
            if current_section and current_data:
                sections[current_section] = '\n'.join(current_data)
                if scheduler.debug:
                    print(f"[DEBUG] Saved section '{current_section}' with {len(current_data)} lines")
            current_section = line.replace('=', '').strip()
            current_data = []
        else:
            if line.strip():
                current_data.append(line)

    if current_section and current_data:
        sections[current_section] = '\n'.join(current_data)
        if scheduler.debug:
            print(f"[DEBUG] Saved section '{current_section}' with {len(current_data)} lines")

    return sections

def create_task_instance_id(scheduler, product, task_id, task_type='baseline'):
    """Create a unique task instance ID"""
    if task_type == 'baseline':
        return f"{product}_{task_id}"
    else:
        return f"{task_type}_{task_id}"

def map_mechanic_to_quality_team(scheduler, mechanic_team):
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
        if quality_team in scheduler.quality_team_capacity:
            return quality_team

    print(f"[WARNING] Could not map '{mechanic_team}' to a quality team")
    return None

def load_data_from_csv(scheduler):
    print(f"\n[DEBUG] Starting to load data from {scheduler.csv_file_path}")

    scheduler._dynamic_constraints_cache = None
    scheduler._critical_path_cache = {}

    try:
        with open(scheduler.csv_file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        print("[WARNING] UTF-8 decoding failed, trying latin-1...")
        with open(scheduler.csv_file_path, 'r', encoding='latin-1') as f:
            content = f.read()


    # Remove BOM if present
    if content.startswith('\ufeff'):
        print("[WARNING] Removing BOM from file")
        content = content[1:]

    sections = parse_csv_sections(scheduler, content)
    print(f"[DEBUG] Found {len(sections)} sections in CSV file")

    # CRITICAL: Load shift hours FIRST from CSV
    _load_shift_hours(scheduler, sections)

    # Then load team capacities (which use shifts)
    _load_team_capacities_and_schedules(scheduler, sections)

    # ADD THIS: Load customer team capacities and schedules
    _load_customer_teams(scheduler, sections)

    # Then load task relationships and definitions
    _load_task_definitions(scheduler, sections)

    # Load product lines and create instances
    _load_product_lines(scheduler, sections)

    # Now load quality inspections (team mapping will work now)
    _load_quality_inspections(scheduler, sections)

    # ADD THIS: Load customer inspections
    _load_customer_inspections(scheduler, sections)

    # Load late parts and rework
    _load_late_parts_and_rework(scheduler, sections)

    # Load remaining data (holidays, etc.)
    _load_holidays(scheduler, sections)

    # Validate and fix quality team assignments
    _validate_and_fix_quality_assignments(scheduler)

    _print_summary(scheduler)

def _load_customer_inspections(scheduler, sections):
    """Load customer inspection requirements"""

    if "CUSTOMER INSPECTION REQUIREMENTS" in sections:
        reader = csv.reader(sections["CUSTOMER INSPECTION REQUIREMENTS"].splitlines())
        cc_count = 0

        for row in reader:
            if row and row[0] != 'Primary Task':
                primary_task_id = int(row[0].strip())
                cc_task_id = row[1].strip()  # e.g., "CC_601"
                cc_headcount = int(row[2].strip())
                cc_duration = int(
                    row[3].strip())  # Note: column is named "Quality Duration" but it's customer duration

                # Create customer inspection for each product
                for product in scheduler.delivery_dates.keys():
                    start_task, end_task = scheduler.product_remaining_ranges.get(product, (1, 100))

                    if start_task <= primary_task_id <= end_task:
                        primary_instance_id = scheduler.task_instance_map.get((product, primary_task_id))

                        if primary_instance_id:
                            cc_instance_id = f"{product}_{cc_task_id}"

                            scheduler.tasks[cc_instance_id] = {
                                'duration': cc_duration,
                                'team': 'Customer Team 1',  # Will be assigned dynamically during scheduling
                                'team_skill': 'Customer Team 1',  # Default, will be reassigned
                                'team_type': 'customer',
                                'mechanics_required': cc_headcount,
                                'is_quality': False,
                                'is_customer': True,
                                'task_type': 'Customer',  # Just "Customer" not "Customer Inspection"
                                'primary_task': primary_instance_id,
                                'product': product,
                                'original_task_id': cc_task_id
                            }

                            scheduler.customer_inspections[cc_instance_id] = {
                                'primary_task': primary_instance_id,
                                'headcount': cc_headcount
                            }

                            scheduler.customer_requirements[primary_instance_id] = cc_instance_id
                            scheduler.instance_to_product[cc_instance_id] = product
                            scheduler.instance_to_original_task[cc_instance_id] = cc_task_id
                            cc_count += 1

        print(f"[DEBUG] Created {cc_count} customer inspection instances")


def _load_shift_hours(scheduler, sections):
    """Load shift working hours from CSV"""
    if "SHIFT WORKING HOURS" in sections:
        reader = csv.reader(sections["SHIFT WORKING HOURS"].splitlines())

        # Initialize shift_hours dict
        scheduler.shift_hours = {}

        for row in reader:
            if row and row[0] != 'Shift' and len(row) >= 3:
                shift_name = row[0].strip()
                start_time = row[1].strip()
                end_time = row[2].strip()

                scheduler.shift_hours[shift_name] = {
                    'start': start_time,
                    'end': end_time
                }

        print(f"[DEBUG] Loaded shift hours for {len(scheduler.shift_hours)} shifts:")
        for shift, hours in scheduler.shift_hours.items():
            print(f"  - {shift}: {hours['start']} to {hours['end']}")
    else:
        # Fallback to defaults if not in CSV
        print("[WARNING] SHIFT WORKING HOURS not found in CSV, using defaults")
        scheduler.shift_hours = {
            '1st': {'start': '6:00', 'end': '14:30'},
            '2nd': {'start': '14:30', 'end': '23:00'},
            '3rd': {'start': '23:00', 'end': '6:30'}
        }

    # Create alias for compatibility
    scheduler.shift_definitions = scheduler.shift_hours

def _load_team_capacities_and_schedules(scheduler, sections):
    """Load team capacities and working schedules with skill-specific shift inheritance"""

    # Load mechanic team capacities
    if "MECHANIC TEAM CAPACITY" in sections:
        reader = csv.reader(sections["MECHANIC TEAM CAPACITY"].splitlines())
        base_team_capacities = defaultdict(int)
        for row in reader:
            if row and row[0] != 'Mechanic Team':
                team = row[0].strip()
                capacity = int(row[1].strip())
                scheduler.team_capacity[team] = capacity
                scheduler._original_team_capacity[team] = capacity

                # Aggregate into base team
                base_team_name = team.split(' (')[0]
                base_team_capacities[base_team_name] += capacity

        # Add aggregated base team capacities to the scheduler
        for team, capacity in base_team_capacities.items():
            if team not in scheduler.team_capacity:
                scheduler.team_capacity[team] = capacity
                scheduler._original_team_capacity[team] = capacity

        print(f"[DEBUG] Loaded capacity for {len(scheduler.team_capacity)} mechanic teams (including aggregated base teams)")
        print(f"[DEBUG] Mechanic Team 4 capacity: {scheduler.team_capacity.get('Mechanic Team 4')}")

    # Load quality team capacities
    if "QUALITY TEAM CAPACITY" in sections:
        reader = csv.reader(sections["QUALITY TEAM CAPACITY"].splitlines())
        for row in reader:
            if row and row[0] != 'Quality Team':
                team = row[0].strip()
                capacity = int(row[1].strip())
                scheduler.quality_team_capacity[team] = capacity
                scheduler._original_quality_capacity[team] = capacity
        print(f"[DEBUG] Loaded capacity for {len(scheduler.quality_team_capacity)} quality teams")

    # Load mechanic team shifts - STORE AS LISTS
    if "MECHANIC TEAM WORKING CALENDARS" in sections:
        reader = csv.reader(sections["MECHANIC TEAM WORKING CALENDARS"].splitlines())
        for row in reader:
            if row and row[0] != 'Mechanic Team':
                team = row[0].strip()
                shifts = row[1].strip()
                scheduler.team_shifts[team] = [shifts]  # Store as list!
        print(f"[DEBUG] Loaded {len(scheduler.team_shifts)} mechanic team schedules")

    # Load quality team shifts - STORE AS LISTS
    scheduler.quality_team_shifts = {}
    if "QUALITY TEAM WORKING CALENDARS" in sections:
        reader = csv.reader(sections["QUALITY TEAM WORKING CALENDARS"].splitlines())
        for row in reader:
            if row and row[0] != 'Quality Team':
                team = row[0].strip()
                shifts = row[1].strip()
                scheduler.quality_team_shifts[team] = [shifts]  # Store as list!
        print(f"[DEBUG] Loaded {len(scheduler.quality_team_shifts)} quality team schedules")

    # Ensure ALL quality teams have shifts
    for team in scheduler.quality_team_capacity:
        if team not in scheduler.quality_team_shifts or not scheduler.quality_team_shifts[team]:
            match = re.search(r'(\d+)', team)
            if match:
                team_number = match.group(1)
                mechanic_base = f'Mechanic Team {team_number}'
                if mechanic_base in scheduler.team_shifts:
                    # Copy the list, not just reference
                    scheduler.quality_team_shifts[team] = scheduler.team_shifts[mechanic_base].copy()
                    if scheduler.debug:
                        print(
                            f"[DEBUG] Quality {team} inheriting shift {scheduler.team_shifts[mechanic_base]} from {mechanic_base}")
                else:
                    # Default based on team number pattern
                    team_num = int(team_number)
                    if team_num in [1, 4, 7, 10]:
                        scheduler.quality_team_shifts[team] = ["1st"]
                    elif team_num in [2, 5, 8]:
                        scheduler.quality_team_shifts[team] = ["2nd"]
                    else:
                        scheduler.quality_team_shifts[team] = ["3rd"]
            else:
                scheduler.quality_team_shifts[team] = ["1st"]

    # Map shifts from base teams to skill-specific teams
    shifts_inherited = 0
    for team_name in list(scheduler.team_capacity.keys()):
        if " (Skill " in team_name and team_name not in scheduler.team_shifts:
            base_team = team_name.split(" (Skill")[0]
            if base_team in scheduler.team_shifts:
                # Copy the list from base team
                scheduler.team_shifts[team_name] = scheduler.team_shifts[base_team].copy()
                shifts_inherited += 1
            else:
                # Default to 1st shift as list
                scheduler.team_shifts[team_name] = ["1st"]

    if shifts_inherited > 0:
        print(f"[DEBUG] Inherited shifts for {shifts_inherited} skill-specific mechanic teams")

    # Final validation
    print(f"[DEBUG] Final shift assignments:")
    print(f"  - Mechanic teams with shifts: {len([t for t in scheduler.team_shifts if scheduler.team_shifts[t]])}")
    print(
        f"  - Quality teams with shifts: {len([t for t in scheduler.quality_team_shifts if scheduler.quality_team_shifts[t]])}")


def _load_customer_teams(scheduler, sections):
    """Load customer team capacities and schedules"""

    # Load customer team capacities
    if "CUSTOMER TEAM CAPACITY" in sections:
        reader = csv.reader(sections["CUSTOMER TEAM CAPACITY"].splitlines())
        base_team_capacities = defaultdict(int)
        for row in reader:
            if row and row[0] != 'Customer Team':
                team = row[0].strip()
                capacity = int(row[1].strip())
                scheduler.customer_team_capacity[team] = capacity
                scheduler._original_customer_team_capacity[team] = capacity

                # Aggregate into base team
                base_team_name = team.split(' (')[0]
                base_team_capacities[base_team_name] += capacity

        # Add aggregated base team capacities to the scheduler
        for team, capacity in base_team_capacities.items():
            if team not in scheduler.customer_team_capacity:
                scheduler.customer_team_capacity[team] = capacity
                scheduler._original_customer_team_capacity[team] = capacity

        print(f"[DEBUG] Loaded capacity for {len(scheduler.customer_team_capacity)} customer teams (including aggregated base teams)")

    # Load customer team shifts
    if "CUSTOMER TEAM WORKING CALENDARS" in sections:
        reader = csv.reader(sections["CUSTOMER TEAM WORKING CALENDARS"].splitlines())
        for row in reader:
            if row and row[0] != 'Customer Team':
                team = row[0].strip()
                shifts = row[1].strip()
                scheduler.customer_team_shifts[team] = [shifts]  # Store as list for consistency
        print(f"[DEBUG] Loaded {len(scheduler.customer_team_shifts)} customer team schedules")


def _load_task_definitions(scheduler, sections):
    """Load task relationships and definitions"""

    # Load Task Relationships
    if "TASK RELATIONSHIPS TABLE" in sections:
        df = pd.read_csv(StringIO(sections["TASK RELATIONSHIPS TABLE"]))
        df.columns = df.columns.str.strip()
        for col in ['First', 'Second']:
            if col in df.columns:
                df[col] = df[col].astype(int)

        if 'Relationship Type' not in df.columns and 'Relationship' not in df.columns:
            df['Relationship Type'] = 'Finish <= Start'
        elif 'Relationship' in df.columns and 'Relationship Type' not in df.columns:
            df['Relationship Type'] = df['Relationship']

        scheduler.precedence_constraints = df.to_dict('records')
        print(f"[DEBUG] Loaded {len(scheduler.precedence_constraints)} baseline task relationships")

    # Load Task Duration and Resources
    if "TASK DURATION AND RESOURCE TABLE" in sections:
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

                scheduler.baseline_task_data[task_id] = {
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
            for task_data in scheduler.baseline_task_data.values():
                skill_counts[task_data['team_skill']] += 1
            print(f"[DEBUG] Task distribution across team-skill combinations:")
            for team_skill, count in sorted(skill_counts.items()):
                print(f"  - {team_skill}: {count} tasks")

    # Now, process the precedence constraints to add dependencies to baseline tasks
    if hasattr(scheduler, 'precedence_constraints'):
        # Initialize dependencies list for all baseline tasks
        for task_id in scheduler.baseline_task_data:
            scheduler.baseline_task_data[task_id]['dependencies'] = []

        # Populate dependencies
        dep_count = 0
        for constraint in scheduler.precedence_constraints:
            predecessor = constraint['First']
            successor = constraint['Second']
            if successor in scheduler.baseline_task_data:
                # Ensure the predecessor is also a valid task
                if predecessor in scheduler.baseline_task_data:
                    scheduler.baseline_task_data[successor]['dependencies'].append(predecessor)
                    dep_count += 1
        print(f"[DEBUG] Applied {dep_count} dependency relationships to baseline tasks")


def _load_product_lines(scheduler, sections):
    """Load product lines and create task instances"""

    # Load Product Line Delivery Schedule
    if "PRODUCT LINE DELIVERY SCHEDULE" in sections:
        df = pd.read_csv(StringIO(sections["PRODUCT LINE DELIVERY SCHEDULE"]))
        df.columns = df.columns.str.strip()
        for _, row in df.iterrows():
            product = row['Product Line'].strip()
            scheduler.delivery_dates[product] = pd.to_datetime(row['Delivery Date'])
        print(f"[DEBUG] Loaded delivery dates for {len(scheduler.delivery_dates)} product lines")

    # Load Product Line Jobs and CREATE TASK INSTANCES
    if "PRODUCT LINE JOBS" in sections:
        df = pd.read_csv(StringIO(sections["PRODUCT LINE JOBS"]))
        df.columns = df.columns.str.strip()

        print(f"\n[DEBUG] Creating task instances for each product...")
        total_instances = 0

        for _, row in df.iterrows():
            product = row['Product Line'].strip()
            start_task = int(row['Task Start'])
            end_task = int(row['Task End'])

            scheduler.product_remaining_ranges[product] = (start_task, end_task)

            product_instances = 0
            for task_id in range(start_task, end_task + 1):
                if task_id in scheduler.baseline_task_data:
                    instance_id = create_task_instance_id(scheduler, product, task_id, 'baseline')
                    # Copy ALL fields from baseline_task_data including team, skill, and team_skill
                    task_data = scheduler.baseline_task_data[task_id].copy()
                    task_data['product'] = product
                    task_data['original_task_id'] = task_id

                    scheduler.tasks[instance_id] = task_data
                    scheduler.task_instance_map[(product, task_id)] = instance_id
                    scheduler.instance_to_product[instance_id] = product
                    scheduler.instance_to_original_task[instance_id] = task_id

                    product_instances += 1
                    total_instances += 1

            completed = start_task - 1 if start_task > 1 else 0
            print(f"[DEBUG]   {product}: Created {product_instances} instances (tasks {start_task}-{end_task})")
            print(f"           Already completed: tasks 1-{completed}")

        print(f"[DEBUG] Total baseline task instances created: {total_instances}")

        # Debug: Show sample of team-skill distribution in instances
        if total_instances > 0:
            team_skill_instance_counts = defaultdict(int)
            for task_info in scheduler.tasks.values():
                if 'team_skill' in task_info:
                    team_skill_instance_counts[task_info['team_skill']] += 1

            if team_skill_instance_counts:
                print(f"\n[DEBUG] Instance distribution by team-skill:")
                for team_skill, count in sorted(team_skill_instance_counts.items())[:10]:  # Show first 10
                    print(f"  - {team_skill}: {count} instances")


def _load_quality_inspections(scheduler, sections):
    """Load quality inspections - team capacity should be loaded by now"""

    if "QUALITY INSPECTION REQUIREMENTS" in sections:
        df = pd.read_csv(StringIO(sections["QUALITY INSPECTION REQUIREMENTS"]))
        df.columns = df.columns.str.strip()
        qi_count = 0
        qi_without_team = 0

        for _, row in df.iterrows():
            primary_task_id = int(row['Primary Task'])
            qi_task_id = int(row['Quality Task'])

            for product in scheduler.delivery_dates.keys():
                start_task, end_task = scheduler.product_remaining_ranges.get(product, (1, 100))

                if start_task <= primary_task_id <= end_task:
                    primary_instance_id = scheduler.task_instance_map.get((product, primary_task_id))
                    if primary_instance_id:
                        # Get the primary task's team
                        primary_task_info = scheduler.tasks.get(primary_instance_id, {})
                        primary_team = primary_task_info.get('team', '')

                        # Map mechanic team to quality team (1:1 mapping)
                        quality_team = map_mechanic_to_quality_team(scheduler, primary_team)

                        if not quality_team:
                            qi_without_team += 1
                            if scheduler.debug:
                                print(
                                    f"[WARNING] No quality team for QI of task {primary_instance_id} (team: {primary_team})")

                        qi_instance_id = f"{product}_QI_{qi_task_id}"

                        scheduler.tasks[qi_instance_id] = {
                            'duration': int(row['Quality Duration (minutes)']),
                            'team': quality_team,
                            'mechanics_required': int(row['Quality Headcount Required']),
                            'is_quality': True,
                            'task_type': 'Quality Inspection',
                            'primary_task': primary_instance_id,
                            'product': product,
                            'original_task_id': qi_task_id
                        }

                        scheduler.quality_inspections[qi_instance_id] = {
                            'primary_task': primary_instance_id,
                            'headcount': int(row['Quality Headcount Required'])
                        }

                        scheduler.quality_requirements[primary_instance_id] = qi_instance_id
                        scheduler.instance_to_product[qi_instance_id] = product
                        scheduler.instance_to_original_task[qi_instance_id] = qi_task_id
                        qi_count += 1

        print(f"[DEBUG] Created {qi_count} quality inspection instances")
        if qi_without_team > 0:
            print(f"[WARNING] {qi_without_team} QI tasks could not be assigned teams")


def _load_holidays(scheduler, sections):
    """Load holiday calendar"""

    if "PRODUCT LINE HOLIDAY CALENDAR" in sections:
        df = pd.read_csv(StringIO(sections["PRODUCT LINE HOLIDAY CALENDAR"]))
        df.columns = df.columns.str.strip()
        holiday_count = 0

        for _, row in df.iterrows():
            try:
                product = row['Product Line'].strip()
                holiday_date = pd.to_datetime(row['Date'])
                scheduler.holidays[product].add(holiday_date)
                holiday_count += 1
            except (ValueError, KeyError) as e:
                print(f"[WARNING] Error processing holiday row: {row}, Error: {e}")
                continue
        print(f"[DEBUG] Loaded {holiday_count} holiday entries")


def _load_late_parts_and_rework(scheduler, sections):
    """Load late parts and rework tasks with team/skill inherited from dependent baseline tasks"""

    # First load all constraints to understand the dependency structure

    # Load Late Parts Relationships
    if "LATE PARTS RELATIONSHIPS TABLE" in sections:
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

                scheduler.late_part_constraints.append({
                    'First': first_task,
                    'Second': second_task,
                    'Relationship': relationship,
                    'On_Dock_Date': on_dock_date,
                    'Product_Line': product_line
                })

                scheduler.on_dock_dates[first_task] = on_dock_date
                lp_count += 1
            except (ValueError, KeyError) as e:
                print(f"[WARNING] Error processing late part relationship row: {row}, Error: {e}")
                continue
        print(f"[DEBUG] Loaded {lp_count} late part relationships")

    # Load Rework Relationships
    if "REWORK RELATIONSHIPS TABLE" in sections:
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

                scheduler.rework_constraints.append({
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
                    if (current_product, task_num) in scheduler.task_instance_map:
                        # Found a baseline task!
                        instance_id = scheduler.task_instance_map[(current_product, task_num)]
                        if instance_id in scheduler.tasks:
                            return scheduler.tasks[instance_id], instance_id
                else:
                    # Try to find in any product
                    for prod in scheduler.delivery_dates.keys():
                        if (prod, task_num) in scheduler.task_instance_map:
                            instance_id = scheduler.task_instance_map[(prod, task_num)]
                            if instance_id in scheduler.tasks:
                                return scheduler.tasks[instance_id], instance_id

            # Look for what this task is a predecessor to
            found_successor = False

            # Check late part constraints
            for constraint in scheduler.late_part_constraints:
                if constraint['First'] == current_task:
                    next_task = constraint['Second']
                    next_product = constraint.get('Product_Line', current_product)
                    to_check.append((next_task, next_product))
                    found_successor = True

            # Check rework constraints
            for constraint in scheduler.rework_constraints:
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
                for constraint in scheduler.late_part_constraints:
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

                    if scheduler.debug:
                        print(
                            f"[DEBUG] Late part {task_id} inheriting team/skill from {baseline_instance_id}: {team_skill}")
                    lp_inherited_count += 1
                else:
                    # Fallback to CSV-defined team or default
                    base_team = row['Resource Type'].strip()
                    skill = 'Skill 1'  # Default skill
                    team_skill = f"{base_team} ({skill})"

                    # Verify this team+skill exists in capacity
                    if team_skill not in scheduler.team_capacity:
                        # Find first available skill for this base team
                        for cap_team in scheduler.team_capacity:
                            if cap_team.startswith(base_team + " ("):
                                team_skill = cap_team
                                # Extract skill from team_skill
                                skill_match = re.search(r'\((.*?)\)', team_skill)
                                if skill_match:
                                    skill = skill_match.group(1)
                                break

                    if scheduler.debug:
                        print(f"[WARNING] Late part {task_id} could not inherit team/skill, using {team_skill}")

                instance_id = task_id

                scheduler.tasks[instance_id] = {
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

                scheduler.late_part_tasks[instance_id] = True
                if product:
                    scheduler.instance_to_product[instance_id] = product
                scheduler.instance_to_original_task[instance_id] = task_id

                lp_task_count += 1
            except (ValueError, KeyError) as e:
                print(f"[WARNING] Error processing late part task row: {row}, Error: {e}")
                continue

        print(
            f"[DEBUG] Created {lp_task_count} late part task instances ({lp_inherited_count} inherited team/skill)")

    # Load Rework Task Details
    if "REWORK TASK DETAILS" in sections:
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
                for constraint in scheduler.rework_constraints:
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

                    if scheduler.debug:
                        print(
                            f"[DEBUG] Rework {task_id} inheriting team/skill from {baseline_instance_id}: {team_skill}")
                    rw_inherited_count += 1
                else:
                    # Fallback to CSV-defined team or default
                    base_team = row['Resource Type'].strip()
                    skill = 'Skill 1'  # Default skill
                    team_skill = f"{base_team} ({skill})"

                    # Verify this team+skill exists in capacity
                    if team_skill not in scheduler.team_capacity:
                        # Find first available skill for this base team
                        for cap_team in scheduler.team_capacity:
                            if cap_team.startswith(base_team + " ("):
                                team_skill = cap_team
                                # Extract skill from team_skill
                                skill_match = re.search(r'\((.*?)\)', team_skill)
                                if skill_match:
                                    skill = skill_match.group(1)
                                break

                    if scheduler.debug:
                        print(f"[WARNING] Rework {task_id} could not inherit team/skill, using {team_skill}")

                instance_id = task_id

                scheduler.tasks[instance_id] = {
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

                scheduler.rework_tasks[instance_id] = True
                if product:
                    scheduler.instance_to_product[instance_id] = product
                scheduler.instance_to_original_task[instance_id] = task_id

                # Check if rework task needs quality inspection
                needs_qi = row.get('Needs QI', 'Yes').strip() if pd.notna(row.get('Needs QI')) else 'Yes'
                qi_duration = int(row['QI Duration (minutes)']) if pd.notna(
                    row.get('QI Duration (minutes)')) else 30
                qi_headcount = int(row['QI Headcount']) if pd.notna(row.get('QI Headcount')) else 1

                if needs_qi.lower() in ['yes', 'y', '1', 'true']:
                    qi_instance_id = f"QI_{task_id}"

                    # Get the quality team based on the rework task's base team
                    quality_team = map_mechanic_to_quality_team(scheduler, base_team)

                    scheduler.quality_requirements[instance_id] = qi_instance_id

                    scheduler.tasks[qi_instance_id] = {
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

                    scheduler.quality_inspections[qi_instance_id] = {
                        'primary_task': instance_id,
                        'headcount': qi_headcount
                    }

                    if product:
                        scheduler.instance_to_product[qi_instance_id] = product
                    scheduler.instance_to_original_task[qi_instance_id] = qi_instance_id

                    rw_qi_count += 1

                rw_task_count += 1
            except (ValueError, KeyError) as e:
                print(f"[WARNING] Error processing rework task row: {row}, Error: {e}")
                continue

        print(f"[DEBUG] Created {rw_task_count} rework task instances ({rw_inherited_count} inherited team/skill)")
        if rw_qi_count > 0:
            print(f"[DEBUG] Created {rw_qi_count} quality inspections for rework tasks")


def _validate_and_fix_quality_assignments(scheduler):
    """Validate and fix all quality inspection team assignments"""
    qi_without_teams = 0
    qi_fixed = 0
    qi_with_teams = {}

    for task_id, task_info in scheduler.tasks.items():
        if task_info.get('is_quality', False):
            team = task_info.get('team')
            if not team:
                qi_without_teams += 1
                # Try to fix it
                if task_id in scheduler.quality_inspections:
                    primary_task_id = scheduler.quality_inspections[task_id].get('primary_task')
                    if primary_task_id and primary_task_id in scheduler.tasks:
                        primary_team = scheduler.tasks[primary_task_id].get('team')
                        quality_team = map_mechanic_to_quality_team(scheduler, primary_team)
                        if quality_team:
                            task_info['team'] = quality_team
                            qi_fixed += 1
                            if scheduler.debug:
                                print(f"[FIX] Assigned {quality_team} to orphaned QI {task_id}")
            else:
                if team not in qi_with_teams:
                    qi_with_teams[team] = 0
                qi_with_teams[team] += 1

    if qi_fixed > 0:
        print(f"[DEBUG] Fixed {qi_fixed} quality inspection team assignments")

    if qi_without_teams - qi_fixed > 0:
        print(f"[WARNING] {qi_without_teams - qi_fixed} QI tasks still without teams!")


def _print_summary(scheduler):
    """Print comprehensive summary of loaded data"""
    print(f"\n" + "=" * 80)
    print("DATA LOADING SUMMARY")
    print("=" * 80)

    task_type_counts = defaultdict(int)
    product_task_counts = defaultdict(int)

    for instance_id, task_info in scheduler.tasks.items():
        task_type_counts[task_info['task_type']] += 1
        if 'product' in task_info and task_info['product']:
            product_task_counts[task_info['product']] += 1

    print(f"\n[DEBUG] Task Instance Summary:")
    print(f"Total task instances: {len(scheduler.tasks)}")
    print("\nBreakdown by type:")
    for task_type, count in sorted(task_type_counts.items()):
        print(f"  - {task_type}: {count}")

    print(f"\n[DEBUG] Task instances per product:")
    for product in sorted(scheduler.delivery_dates.keys()):
        count = product_task_counts.get(product, 0)
        start, end = scheduler.product_remaining_ranges.get(product, (0, 0))
        print(f"  - {product}: {count} instances (baseline tasks {start}-{end})")

    if scheduler.late_part_tasks:
        print(f"\n[DEBUG] Late Part Tasks:")
        print(f"  - Total late part tasks: {len(scheduler.late_part_tasks)}")
        print(f"  - Late part constraints: {len(scheduler.late_part_constraints)}")

        lp_by_product = defaultdict(int)
        for task_id in scheduler.late_part_tasks:
            product = scheduler.instance_to_product.get(task_id, 'Unassigned')
            lp_by_product[product] += 1

        for product, count in sorted(lp_by_product.items()):
            print(f"    {product}: {count} late part tasks")

    if scheduler.rework_tasks:
        print(f"\n[DEBUG] Rework Tasks:")
        print(f"  - Total rework tasks: {len(scheduler.rework_tasks)}")
        print(f"  - Rework constraints: {len(scheduler.rework_constraints)}")

        rw_by_product = defaultdict(int)
        for task_id in scheduler.rework_tasks:
            product = scheduler.instance_to_product.get(task_id, 'Unassigned')
            rw_by_product[product] += 1

        for product, count in sorted(rw_by_product.items()):
            print(f"    {product}: {count} rework tasks")

    if scheduler.quality_inspections:
        print(f"\n[DEBUG] Quality Inspections:")
        print(f"  - Total QI instances: {len(scheduler.quality_inspections)}")
        print(f"  - Tasks requiring QI: {len(scheduler.quality_requirements)}")

    print(f"\n[DEBUG] Resources:")
    print(f"  - Mechanic teams: {len(scheduler.team_capacity)}")
    total_mechanics = sum(scheduler.team_capacity.values())
    print(f"    Total mechanic capacity: {total_mechanics}")
    for team, capacity in sorted(scheduler.team_capacity.items()):
        shifts = scheduler.team_shifts.get(team, [])
        print(f"    {team}: {capacity} people, shifts: {shifts}")

    print(f"  - Quality teams: {len(scheduler.quality_team_capacity)}")
    total_quality = sum(scheduler.quality_team_capacity.values())
    print(f"    Total quality capacity: {total_quality}")
    for team, capacity in sorted(scheduler.quality_team_capacity.items()):
        shifts = scheduler.quality_team_shifts.get(team, [])
        print(f"    {team}: {capacity} people, shifts: {shifts}")

    print(f"\n[DEBUG] Delivery Schedule:")
    for product, date in sorted(scheduler.delivery_dates.items()):
        print(f"  - {product}: {date.strftime('%Y-%m-%d')}")

    if scheduler.holidays:
        print(f"\n[DEBUG] Holidays:")
        total_holidays = sum(len(dates) for dates in scheduler.holidays.values())
        print(f"  - Total holiday entries: {total_holidays}")
        for product, dates in sorted(scheduler.holidays.items()):
            if dates:
                print(f"    {product}: {len(dates)} holidays")

    print(f"\n[DEBUG] Constraints Summary:")
    print(f"  - Baseline precedence constraints: {len(scheduler.precedence_constraints)}")
    print(f"  - Late part constraints: {len(scheduler.late_part_constraints)}")
    print(f"  - Rework constraints: {len(scheduler.rework_constraints)}")
    total_constraints = (len(scheduler.precedence_constraints) +
                         len(scheduler.late_part_constraints) +
                         len(scheduler.rework_constraints))
    print(f"  - Total constraints defined: {total_constraints}")
    print("=" * 80)
