"""
Google Docs Operations Module
Handles logging operations in Google Docs
"""


def update_log(docs, text):
    """
    Append text to the Google Docs log document.
    
    Args:
        docs: Google Docs API service object
        text: Text to append to the log
    """
    # Document ID extracted from the provided link
    document_id = "1GxDfL0Y5_62HniHOxDS9j1VdHIO22QohkK7UaOT-PtA"
    
    # Fetch the document to determine the end index
    document = docs.documents().get(documentId=document_id).execute()
    content = document.get('body', {}).get('content', [])

    # Find the end index of the document
    end_index = None
    if content:
        last_element = content[-1]
        end_index = last_element.get('endIndex', 1)  
    
    # Nothing was written in the logs yet
    if end_index is None:
        end_index = 2

    # Update content in the document
    requests = [
        {
            'insertText': {
                'location': {
                    'index': end_index - 1, 
                },
                'text': f"{text}"
            }
        }
    ]
    
    # Execute the batch update
    result = docs.documents().batchUpdate(
        documentId=document_id, body={'requests': requests}
    ).execute()

