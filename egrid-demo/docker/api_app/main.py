from fastapi import FastAPI
app = FastAPI()
@app.get('/top')
def top(limit:int=5,state:str|None=None): return [{"plant":"Sample Plant","state":state or 'ALL','net_generation_mwh":12345}]
