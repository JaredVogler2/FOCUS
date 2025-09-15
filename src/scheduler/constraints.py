# src/scheduler/constraints.py

from collections import defaultdict
from . import utils

def build_dynamic_dependencies(scheduler):
    """
    Builds a comprehensive dependency graph. It correctly chains Quality Inspection (QI)
    and Customer (CC) tasks between a predecessor and its successor, using a unified
    and simplified logic for all constraint types (baseline, rework, late-part).
    """
    if scheduler._dynamic_constraints_cache is not None:
        return scheduler._dynamic_constraints_cache

    utils.debug_print(scheduler, f"\n[DEBUG] Building dynamic dependencies with unified chaining logic...")
    dynamic_constraints = []
    processed_predecessors = set()

    # Helper to get instance ID for any task type
    def get_instance_id(task_id, product):
        # Numeric IDs are baseline tasks and need the product prefix
        if str(task_id).isdigit():
            return scheduler.task_instance_map.get((product, int(task_id)))
        # Non-numeric IDs (e.g., 'RW1', 'LP2') are unique and used directly
        return str(task_id)

    # Combine all constraints into one list for unified processing
    all_constraints = (scheduler.precedence_constraints +
                       scheduler.late_part_constraints +
                       scheduler.rework_constraints)

    for constraint in all_constraints:
        first_task_id = constraint['First']
        second_task_id = constraint['Second']
        relationship = utils.normalize_relationship_type(constraint.get('Relationship', 'Finish <= Start'))
        product_scope = [constraint['Product_Line']] if constraint.get('Product_Line') else scheduler.delivery_dates.keys()

        for product in product_scope:
            predecessor_instance = get_instance_id(first_task_id, product)
            successor_instance = get_instance_id(second_task_id, product)

            if not predecessor_instance or not successor_instance:
                continue

            # Start the dependency chain
            current_predecessor = predecessor_instance
            processed_predecessors.add(predecessor_instance)

            # Chain in Quality Inspection (QI) if it exists
            if predecessor_instance in scheduler.quality_requirements:
                qi_instance = scheduler.quality_requirements[predecessor_instance]
                dynamic_constraints.append({
                    'First': current_predecessor, 'Second': qi_instance,
                    'Relationship': 'Finish <= Start', 'Product': product
                })
                current_predecessor = qi_instance

            # Chain in Customer Inspection (CC) after QI if it exists
            if predecessor_instance in scheduler.customer_requirements:
                cc_instance = scheduler.customer_requirements[predecessor_instance]
                dynamic_constraints.append({
                    'First': current_predecessor, 'Second': cc_instance,
                    'Relationship': 'Finish <= Start', 'Product': product
                })
                current_predecessor = cc_instance

            # Add the final link from the end of the chain to the main successor
            dynamic_constraints.append({
                'First': current_predecessor, 'Second': successor_instance,
                'Relationship': relationship, 'Product': product
            })

    # Add inspections for any "terminal" tasks (tasks that are never predecessors)
    all_tasks_requiring_inspection = set(scheduler.quality_requirements.keys()) | set(scheduler.customer_requirements.keys())
    for primary_task in all_tasks_requiring_inspection:
        if primary_task not in processed_predecessors:
            product = scheduler.instance_to_product.get(primary_task)
            # This correctly handles QI, CC, or a QI->CC chain for terminal tasks
            # by calling the helper function once per unique primary task.
            add_chained_dependency(primary_task, None, 'Finish <= Start', product, dynamic_constraints, scheduler)


    utils.debug_print(scheduler, f"[DEBUG] Total dynamic constraints built: {len(dynamic_constraints)}")
    scheduler._dynamic_constraints_cache = dynamic_constraints
    return dynamic_constraints


def add_chained_dependency(predecessor_id, successor_id, relationship, product, constraints_list, scheduler):
    """Helper to chain dependencies, including QI and CC tasks."""
    if not predecessor_id:
        return

    current_predecessor = predecessor_id

    # Chain in Quality Inspection (QI) if it exists
    if predecessor_id in scheduler.quality_requirements:
        qi_instance = scheduler.quality_requirements[predecessor_id]
        constraints_list.append({
            'First': current_predecessor, 'Second': qi_instance,
            'Relationship': 'Finish <= Start', 'Product': product
        })
        current_predecessor = qi_instance

    # Chain in Customer Inspection (CC) after QI if it exists
    if predecessor_id in scheduler.customer_requirements:
        cc_instance = scheduler.customer_requirements[predecessor_id]
        constraints_list.append({
            'First': current_predecessor, 'Second': cc_instance,
            'Relationship': 'Finish <= Start', 'Product': product
        })
        current_predecessor = cc_instance

    # Add the final link if a successor exists
    if successor_id:
        constraints_list.append({
            'First': current_predecessor, 'Second': successor_id,
            'Relationship': relationship, 'Product': product
        })

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

def get_dependency_maps(scheduler):
    """
    Builds and returns both a predecessor and successor map in a single pass,
    using original task IDs for compatibility with the frontend.
    """
    predecessor_map = defaultdict(list)
    successor_map = defaultdict(list)

    dynamic_constraints = build_dynamic_dependencies(scheduler)

    for constraint in dynamic_constraints:
        predecessor_instance = constraint.get('First')
        successor_instance = constraint.get('Second')

        if predecessor_instance and successor_instance:
            # Convert instance IDs to original task IDs
            original_pred = scheduler.instance_to_original_task.get(predecessor_instance)
            original_succ = scheduler.instance_to_original_task.get(successor_instance)

            if original_pred and original_succ and original_pred != original_succ:
                # Convert keys and values to strings to prevent sorting errors during JSON serialization
                key_succ = str(original_succ)
                key_pred = str(original_pred)
                val_succ = str(original_succ)
                val_pred = str(original_pred)

                # Add to maps, ensuring no duplicates
                if val_pred not in predecessor_map[key_succ]:
                    predecessor_map[key_succ].append(val_pred)
                if val_succ not in successor_map[key_pred]:
                    successor_map[key_pred].append(val_succ)

    return dict(predecessor_map), dict(successor_map)
