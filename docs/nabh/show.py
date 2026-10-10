import json,sys,re
d=json.load(open('tc_by_element.json',encoding='utf-8'))
n=int(sys.argv[2]) if len(sys.argv)>2 else 1800
for k in d:
    if re.match(sys.argv[1]+'$',k):
        s=d[k]; s=re.sub(r'Select Yes/No|Note/Deviation|Pre-requisite for test|Test Validation|Steps to produce|Expected Outcome','',s)
        s=re.sub(r'\s+',' ',s)
        print('\n##',s[:n])
