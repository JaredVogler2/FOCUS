# src/scheduler/constraints.py

from collections import defaultdict
from . import utils

def build_dynamic_dependencies(scheduler):
    """
    Build dependency graph with support for ALL relationship types and string task IDs
    Including customer inspections with Finish = Start constraints
    """
    if scheduler._dynamic_constraints_cache is not None:
        return scheduler._dynamic_constraints_cache

    utils.debug_print(scheduler, f"\n[DEBUG] Building dynamic dependencies with all relationship types...")
    dynamic_constraints = []

    # 1. Add baseline task constraints (product-specific)
    for constraint in scheduler.precedence_constraints:
        first_task_id = constraint['First']
        second_task_id = constraint['Second']

        relationship = constraint.get('Relationship Type') or constraint.get('Relationship', 'Finish <= Start')
        relationship = utils.normalize_relationship_type(relationship)

        for product in scheduler.delivery_dates.keys():
            first_instance = scheduler.task_instance_map.get((product, first_task_id))
            second_instance = scheduler.task_instance_map.get((product, second_task_id))

            if first_instance and second_instance:
                # Check if first task has quality and/or customer inspections
                has_qi = first_instance in scheduler.quality_requirements
                has_cc = first_instance in scheduler.customer_requirements

                if has_qi and has_cc:
                    # Chain: First -> QI -> CC -> Second
                    qi_instance = scheduler.quality_requirements[first_instance]
                    cc_instance = scheduler.customer_requirements[first_instance]

                    dynamic_constraints.append({
                        'First': first_instance, 'Second': qi_instance,
                        'Relationship': 'Finish = Start', 'Product': product
                    })
                    dynamic_constraints.append({
                        'First': qi_instance, 'Second': cc_instance,
                        'Relationship': 'Finish = Start', 'Product': product
                    })
                    dynamic_constraints.append({
                        'First': cc_instance, 'Second': second_instance,
                        'Relationship': relationship, 'Product': product
                    })

                elif has_qi:
                    # Chain: First -> QI -> Second
                    qi_instance = scheduler.quality_requirements[first_instance]
                    dynamic_constraints.append({
                        'First': first_instance, 'Second': qi_instance,
                        'Relationship': 'Finish = Start', 'Product': product
                    })
                    dynamic_constraints.append({
                        'First': qi_instance, 'Second': second_instance,
                        'Relationship': relationship, 'Product': product
                    })

                elif has_cc:
                    # Chain: First -> CC -> Second
                    cc_instance = scheduler.customer_requirements[first_instance]
                    dynamic_constraints.append({
                        'First': first_instance, 'Second': cc_instance,
                        'Relationship': 'Finish = Start', 'Product': product
                    })
                    dynamic_constraints.append({
                        'First': cc_instance, 'Second': second_instance,
                        'Relationship': relationship, 'Product': product
                    })

                else:
                    # No inspections, direct connection
                    dynamic_constraints.append({
                        'First': first_instance, 'Second': second_instance,
                        'Relationship': relationship, 'Product': product
                    })

    # 2. Add late part constraints
    for lp_constraint in scheduler.late_part_constraints:
        # ... (logic for late part constraints)
        pass

    # 3. Add rework constraints
    for rw_constraint in scheduler.rework_constraints:
        # ... (logic for rework constraints)
        pass

    # 4. Add any remaining inspection constraints
    for primary_instance, qi_instance in scheduler.quality_requirements.items():
        # ... (logic for quality inspection constraints)
        pass
    for primary_instance, cc_instance in scheduler.customer_requirements.items():
        # ... (logic for customer inspection constraints)
        pass

    utils.debug_print(scheduler, f"[DEBUG] Total dynamic constraints: {len(dynamic_constraints)}")

    rel_counts = defaultdict(int)
    for c in dynamic_constraints:
        rel_counts[c['Relationship']] += 1

    if scheduler.debug:
        for rel_type, count in sorted(rel_counts.items()):
            utils.debug_print(scheduler, f"  {rel_type}: {count}")

    scheduler._dynamic_constraints_cache = dynamic_constraints
    return dynamic_constraints

def get_successors(scheduler, task_id):
    """Get all immediate successor tasks for a given task"""
    successors = []
    dynamic_constraints = build_dynamic_dependencies(scheduler)
    for constraint in dynamic_constraints:
        if constraint['First'] == task_id:
            successors.append(constraint['Second'])
    return successors

def get_predecessors(scheduler, task_id):
    """Get all immediate predecessor tasks for a given task"""
    predecessors = []
    dynamic_constraints = build_dynamic_dependencies(scheduler)
    for constraint in dynamic_constraints:
        if constraint['Second'] == task_id:
            predecessors.append(constraint['First'])
    return predecessors
