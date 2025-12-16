from typing import List, Dict


def analyze_species_occurrences(occurrences: List[Dict]) -> List[Dict]:
    """
    Analyze species occurrences and add numeric scores.
    
    Args:
        occurrences: List of dictionaries containing species occurrence data.
                    Each dict should have 'scientificName', 'scientificNameID', 
                    'decimalLongitude', and 'decimalLatitude' keys.
    
    Returns:
        The same list of dictionaries with 'score' field added to each.
    """
    for occurrence in occurrences:
        occurrence['score'] = 0.0
    
    return occurrences

